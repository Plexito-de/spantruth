"""spantruth command line: list, run, report."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from . import (
    __version__,
    doctor,
    doctor_base,
    expectations,
    history,
    pack,
    paths,
    quick,
    report,
    runner,
    scaffold,
    scenarios,
    snapshots,
    subjects,
    watch,
)

# GitHub caps a step summary at 1 MiB. Stay below it, and say where the rest is.
SUMMARY_LIMIT = 1_000_000


def _list(_: argparse.Namespace) -> int:
    print("Scenarios:")
    for sid, sc in scenarios.SCENARIOS.items():
        print(f"  {sid:38} {sc.title}")
    print(f"\nSubjects, from {subjects.SUBJECTS}:")
    for name in subjects.all_names():
        s = subjects.load(name)
        print(f"  {name:38} {s.title} ({', '.join(s.providers)})")
    return 0


def _usage(message: object) -> int:
    print(f"spantruth: {message}", file=sys.stderr)
    return 2


def exit_code(doc: dict, differences: list[expectations.Difference]) -> int:
    """3 when the harness could not run something, else 1 on a difference from the expectations."""
    if runner.incomplete(doc):
        return 3
    return 1 if differences else 0


def _append_summary(path: Path, text: str) -> None:
    if len(text.encode()) > SUMMARY_LIMIT:
        text = text.encode()[:SUMMARY_LIMIT].decode(errors="ignore")
        text += "\n\n... cut at 1,000,000 bytes. The results file has everything.\n"
    with path.open("a") as f:
        f.write(text + "\n")


def _run(args: argparse.Namespace) -> int:
    try:
        selected = [subjects.load(ref) for ref in args.subject or subjects.all_names()]
        overrides = tuple(subjects.resolve_override(spec) for spec in args.overrides or ())
        if args.quick and args.scenario:
            return _usage("--quick and --scenario cannot be used together")
        ids = args.scenario or (quick.scenario_ids() if args.quick else list(scenarios.SCENARIOS))
        for sid in ids:
            scenarios.get(sid)
    except subjects.SubjectError as exc:
        return _usage(exc)
    except KeyError as exc:
        return _usage(exc.args[0])
    if args.comment and len(selected) != 1:
        return _usage("--comment takes exactly one --subject")
    if (args.expect or args.write_expect) and len(selected) != 1:
        return _usage("--expect and --write-expect take exactly one --subject")
    if (args.expect or args.write_expect) and not any(
        scenarios.get(sid).provider in selected[0].providers for sid in ids
    ):
        # Without one cell, a comparison would pass on nothing.
        return _usage(
            f"no selected scenario applies to {selected[0].name}, which covers "
            f"{', '.join(selected[0].providers)}"
        )
    try:
        expected = expectations.load(Path(args.expect)) if args.expect else None
    except expectations.ExpectationError as exc:
        return _usage(exc)
    names = [s.name for s in selected]
    if twice := sorted({n for n in names if names.count(n) > 1}):
        return _usage(f"two subjects named {', '.join(twice)}: rename one of the directories")
    installed = {
        subjects.package_name(r) for s in selected for r in subjects.requirements(s, False)
    }
    for spec in overrides:
        if (name := subjects.package_name(spec)) not in installed:
            return _usage(f"--with {spec}: no selected subject installs {name}")
    ids = scenarios.with_dependencies(ids)
    doc = runner.run(
        selected, ids, Path(args.cache), latest=args.latest, jobs=args.jobs, overrides=overrides
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    text = report.markdown(doc)
    gaps = runner.incomplete(doc)
    differences: list[expectations.Difference] = []
    if args.expect or args.write_expect:
        result = doc["subjects"][selected[0].name]
        found = expectations.failing(result)
    if expected is not None and gaps:
        text += "\n\nThe run is incomplete, so it was not compared with the expectation file."
    elif expected is not None:
        differences = expectations.compare(expected, found, ran=set(result["cells"]))
        skipped = sorted(set(expected) - set(result["cells"]))
        text += "\n\n" + expectations.describe(differences, skipped)
    print(text)
    print(f"\nwrote {out}")
    if args.write_expect and gaps:
        # A gap in the run is not a known failure of the library, so nothing is recorded.
        print("spantruth: the run is incomplete, so --write-expect wrote nothing", file=sys.stderr)
    elif args.write_expect:
        header = (
            f"Known failures of {selected[0].name}, written by spantruth {__version__}.\n"
            "A run with --expect fails when the failing checks differ from this file."
        )
        written = Path(args.write_expect)
        written.parent.mkdir(parents=True, exist_ok=True)
        written.write_text(expectations.dumps(found, header))
        print(f"wrote {written}")
    if args.summary:
        _append_summary(Path(args.summary), text)
    if args.comment:
        compared = differences if expected is not None and not gaps else None
        body = report.comment(doc, selected[0].name, compared, args.comment_link or "")
        Path(args.comment).write_text(body)
    for gap in gaps:
        print(f"spantruth: incomplete run: {gap}", file=sys.stderr)
    return exit_code(doc, differences)


def _doctor(args: argparse.Namespace) -> int:
    if args.fast and args.json:
        return _usage("--fast prints text; leave out --json")
    try:
        if args.fast and (args.base or args.comment or args.summary):
            return _usage(
                "--fast answers from the version table; leave out --base, --comment and --summary"
            )
        if args.fast:
            return history.fast(
                Path(args.path), Path(args.out), Path(args.cache), args.jobs, args.scenario
            )
        if args.base or args.comment or args.summary:
            return doctor_base.run(
                Path(args.path),
                Path(args.out),
                Path(args.cache),
                args.base,
                jobs=args.jobs,
                scenario=args.scenario,
                as_json=args.json,
                comment_file=Path(args.comment) if args.comment else None,
                summary_file=Path(args.summary) if args.summary else None,
                link=args.comment_link or "",
            )
        return doctor.run(
            Path(args.path),
            Path(args.out),
            Path(args.cache),
            jobs=args.jobs,
            scenario=args.scenario,
            as_json=args.json,
        )
    except doctor.DoctorError as exc:
        return _usage(exc)
    except doctor.CannotRun as exc:
        print(f"spantruth: {exc}", file=sys.stderr)
        return 3


def _watch(args: argparse.Namespace) -> int:
    try:
        return watch.run(Path(args.results), Path(args.state), Path(args.out))
    except watch.WatchError as exc:
        print(f"spantruth: could not read the state: {exc}", file=sys.stderr)
        return 3


def _report(args: argparse.Namespace) -> int:
    doc = json.loads(Path(args.results).read_text())
    if args.html:
        Path(args.html).parent.mkdir(parents=True, exist_ok=True)
        Path(args.html).write_text(report.html(doc))
        print(f"wrote {args.html}")
    if args.bugs:
        Path(args.bugs).parent.mkdir(parents=True, exist_ok=True)
        Path(args.bugs).write_text(report.bugs_html(doc))
        print(f"wrote {args.bugs}")
        for name, page in report.bug_pages(doc).items():
            out = Path(args.bugs).parent / name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(page)
            print(f"wrote {out}")
    if args.badges:
        out = Path(args.badges)
        out.mkdir(parents=True, exist_ok=True)
        for name, badge in report.badges(doc).items():
            (out / f"{name}.json").write_text(json.dumps(badge) + "\n")
        print(f"wrote badges to {out}")
    if args.markdown or not (args.html or args.badges or args.bugs):
        print(report.markdown(doc))
    return 0


def parser() -> argparse.ArgumentParser:
    cache = paths.cache_dir()
    parser = argparse.ArgumentParser(
        prog="spantruth",
        description="Check what LLM instrumentation reports against what the provider sent.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list scenarios and subjects").set_defaults(func=_list)
    run = sub.add_parser("run", help="run subjects against scenarios")
    run.add_argument(
        "--subject",
        action="append",
        help="a built-in subject name, or a subject directory: a path with a slash, such as "
        "./spantruth; repeatable",
    )
    run.add_argument("--scenario", action="append", help="scenario id, repeatable")
    run.add_argument(
        "--quick",
        action="store_true",
        help="run one representative scenario per bug class and provider, not every scenario",
    )
    run.add_argument("--latest", action="store_true", help="unpin the subjects' own packages")
    run.add_argument(
        "--with",
        dest="overrides",
        action="append",
        metavar="SPEC",
        help="replace a package with this requirement: a version, a pull request git ref, or a "
        "local checkout (a path with a slash); repeatable",
    )
    run.add_argument(
        "--out",
        default=str(cache / "results" / "latest.json"),
        help="where to write the results document (default: %(default)s)",
    )
    run.add_argument(
        "--cache",
        default=str(cache / "venvs"),
        help="where to keep the subject environments (default: %(default)s)",
    )
    run.add_argument("--jobs", type=int, default=4)
    run.add_argument(
        "--expect",
        metavar="FILE",
        help="compare the failing checks of the one selected subject with this file, and exit 1 "
        "when they differ",
    )
    run.add_argument(
        "--write-expect",
        metavar="FILE",
        help="write the failing checks of the one selected subject to this file",
    )
    run.add_argument(
        "--summary",
        metavar="FILE",
        help="append the Markdown report to this file, such as $GITHUB_STEP_SUMMARY",
    )
    run.add_argument(
        "--comment",
        metavar="FILE",
        help="write a short pull request comment for the one selected subject to this file",
    )
    run.add_argument("--comment-link", metavar="URL", help="the page the comment links to")
    run.set_defaults(func=_run)
    doc = sub.add_parser(
        "doctor",
        help="read your project's lock file and show what your versions get wrong",
        description="Read the versions of a project, run its instrumentation libraries at those "
        "versions against the fake provider, and say what they get wrong. It does not run your "
        "application. Exit code: 0 no problem found, 1 a problem found, 2 nothing to check, 3 "
        "spantruth could not run something.",
    )
    doc.add_argument(
        "path",
        nargs="?",
        default=".",
        help="the project directory, or one dependency file (default: .)",
    )
    doc.add_argument("--json", action="store_true", help="print the result as JSON")
    doc.add_argument(
        "--fast",
        action="store_true",
        help="answer from the version table that ships with spantruth, and run only the "
        "libraries at versions it lacks",
    )
    doc.add_argument("--scenario", action="append", help="scenario id, repeatable")
    doc.add_argument(
        "--out",
        default=str(cache / "results" / "doctor.json"),
        help="where to write the results document (default: %(default)s)",
    )
    doc.add_argument(
        "--cache",
        default=str(cache / "venvs"),
        help="where to keep the subject environments (default: %(default)s)",
    )
    doc.add_argument("--jobs", type=int, default=4)
    doc.add_argument(
        "--base",
        metavar="REF",
        help="compare with the project's dependency files at this git ref, such as origin/main; "
        "exit 1 only when the change adds a finding",
    )
    doc.add_argument(
        "--comment", metavar="FILE", help="write a short pull request comment to this file"
    )
    doc.add_argument("--comment-link", metavar="URL", help="the page the comment links to")
    doc.add_argument(
        "--summary",
        metavar="FILE",
        help="append the comment to this file, such as $GITHUB_STEP_SUMMARY",
    )
    doc.set_defaults(func=_doctor)
    wat = sub.add_parser(
        "watch",
        help="compare a run with the last one, and write the history and the feed",
        description="Compare a results document with the one in --state, add it to the history, "
        "and write history.jsonl, changes.jsonl, feed.xml and changes.html to --out.",
    )
    wat.add_argument("results", help="the results document of this run")
    wat.add_argument(
        "--state",
        required=True,
        help="a directory with the last run's latest.json, history.jsonl and changes.jsonl",
    )
    wat.add_argument("--out", required=True, help="where to write the files")
    wat.set_defaults(func=_watch)
    his = sub.add_parser(
        "history",
        help="run the last releases of each library and write the version table",
        description="Run the last K releases of each tracked package, one package at a time, and "
        "write a table of the failing checks per version, with the first version that fixed each "
        "one. It reads the list of releases from PyPI, so it needs the network. The nightly job "
        "runs it. doctor reads the table and stays offline.",
    )
    history.arguments(his)
    his.set_defaults(func=history.main)
    snap = sub.add_parser(
        "snapshot",
        help="keep a dated copy of the rendered report",
        description="Copy the rendered report into SITE/snapshots/<date>/, or forward the date to "
        "the last copy when the results did not change, and write the index. --state holds the "
        "published snapshots/ with the copies to keep.",
    )
    snapshots.arguments(snap)
    snap.set_defaults(func=snapshots.main)
    rep = sub.add_parser("report", help="render a results document")
    rep.add_argument("results")
    rep.add_argument("--html", help="write a static HTML report to this path")
    rep.add_argument(
        "--badges", help="write a shields.io endpoint JSON per library to this directory"
    )
    rep.add_argument(
        "--bugs",
        help="write the page of bug classes to this path, and the site pages that go with it, "
        "such as fixes.html, to the same directory",
    )
    rep.add_argument("--markdown", action="store_true", help="print the Markdown matrix")
    rep.set_defaults(func=_report)
    exp = sub.add_parser(
        "export",
        help="write every scenario as JSON, for a driver in another language",
        description="Write the scenario pack: each scenario's call plan, the responses of the "
        "fake provider, and the truth. See docs/scenario-pack.md.",
    )
    exp.add_argument("--out", metavar="FILE", help="write to this file (default: stdout)")
    exp.add_argument(
        "--calls-only",
        action="store_true",
        help="write only the call plans, keyed by scenario ID",
    )
    exp.set_defaults(func=_export)
    new = sub.add_parser("new", help="start a new subject from a template")
    kinds = new.add_subparsers(dest="kind", required=True)
    new_subject = kinds.add_parser(
        "subject",
        help="write subject.toml and instrument.py for a new subject",
        description="Write <dir>/<name>/subject.toml and instrument.py. The template loads as it "
        "is. Its instrument() attaches nothing, so every cell is missing a span until you add the "
        "library's setup.",
    )
    new_subject.add_argument("name", help="the subject name: lowercase letters, digits, dashes")
    new_subject.add_argument(
        "--dir",
        default=None,
        help="the directory to create the subject in (default: the built-in subjects in a clone "
        "of spantruth, the current directory otherwise)",
    )
    new_subject.set_defaults(func=_new_subject)
    return parser


def _export(args: argparse.Namespace) -> int:
    text = pack.dumps(pack.calls_only() if args.calls_only else pack.export())
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


def _new_subject_dir(given: str | None, builtin: Path | None = None) -> Path:
    """Where `new subject` writes. An installed spantruth keeps its subjects inside the package,
    in a cache the user cannot find or keep, so outside a clone the default is the current
    directory."""
    builtin = subjects.SUBJECTS if builtin is None else builtin
    if given is not None:
        return Path(given)
    installed = builtin.resolve().is_relative_to(paths.PACKAGE)
    return Path.cwd() if installed else builtin


def _new_subject(args: argparse.Namespace) -> int:
    try:
        directory = scaffold.new_subject(args.name, _new_subject_dir(args.dir))
    except scaffold.ScaffoldError as exc:
        return _usage(exc)
    print(f"wrote {directory / 'subject.toml'}")
    print(f"wrote {directory / 'instrument.py'}")
    builtin = directory.parent.resolve() == subjects.SUBJECTS.resolve()
    ref = directory.name if builtin else str(directory)
    if not builtin and not subjects.is_path(ref):
        ref = f"./{ref}"
    print(f"next: fill in both files, then run: spantruth run --subject {ref}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # unexpected means the harness failed, never a verdict (exit 3)
        traceback.print_exc()
        print(f"spantruth: could not finish: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
