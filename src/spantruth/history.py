"""The version history: which recent releases of each library fail which checks.

The nightly job runs the last few releases of every tracked package, one package at a time, with
everything else at its pin, and writes one compact table, `versions.json`. From the table it reads
"first fixed in": the first release from which a check passes and keeps passing.

Only this command reads the network, and only for the list of releases on PyPI. The harness and
`doctor` stay offline: `doctor` reads the table that ships in the wheel.

A version's result changes only when the grading changes. So an entry of an earlier table is
reused while its suite fingerprint is the same, and a night with no new release runs nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__, bugclasses, paths, runner, scenarios, subjects

SCHEMA = 1
TABLE = paths.data("versions.json")
PYPI = "https://pypi.org/pypi"
DEFAULT_RELEASES = 5
# One release of one package costs about 3 CPU-minutes for 45 cells. 10 releases of the 14 packages
# is about 7 CPU-hours, which a 4-core runner just fits in the job limit of 6 hours.
MAX_RELEASES = 10
# Modules that render or read results. A change in them does not change a verdict, so it does not
# invalidate the entries of an earlier table.
PRESENTATION = {
    "cli",
    "doctor",
    "history",
    "impact",
    "paths",
    "report",
    "site",
    "snapshots",
    "watch",
}
_VERSION = re.compile(r"(\d+(?:\.\d+)*)(?:(a|b|rc)(\d+))?(?:\.post(\d+))?(?:\.dev(\d+))?")
_PHASE = {"a": 0, "b": 1, "rc": 2, None: 3}


class HistoryError(ValueError):
    """A table that cannot be read, or a request that the table cannot answer."""


def version_key(version: str) -> tuple | None:
    """A sort key for a PEP 440 release such as 1.3b0 or 2.0.post1. None for anything else."""
    m = _VERSION.fullmatch(version.strip().lower().removeprefix("v"))
    if not m:
        return None
    release = tuple(int(p) for p in m[1].split("."))
    while len(release) > 1 and release[-1] == 0:
        release = release[:-1]
    dev = int(m[5]) if m[5] is not None else None
    # A dev release sorts before the release it leads to; a post release after it.
    return (
        release,
        _PHASE[m[2]] if not (dev is not None and m[2] is None and m[4] is None) else -1,
        int(m[3] or 0),
        int(m[4]) if m[4] is not None else -1,
        dev if dev is not None else sys.maxsize,
    )


def is_final(version: str) -> bool:
    return bool(re.fullmatch(r"\d+(\.\d+)*(\.post\d+)?", version.strip()))


def recent(pypi: dict[str, Any], k: int) -> list[tuple[str, str]]:
    """The last k releases on PyPI as (version, release date), oldest first.

    A release with no file, or with every file yanked, is not a release. Pre-releases count only
    when the package has no final release, as pip does.
    """
    found = []
    for version, files in pypi.get("releases", {}).items():
        live = [f for f in files if not f.get("yanked")]
        if not live or version_key(version) is None:
            continue
        date = min(f.get("upload_time_iso_8601", "") or "" for f in live)[:10]
        found.append((version, date))
    finals = [r for r in found if is_final(r[0])]
    chosen = sorted(finals or found, key=lambda r: version_key(r[0]))
    return chosen[-k:] if k else []


def fetch(package: str, index: str = PYPI) -> dict[str, Any]:
    """The PyPI JSON document of a package. This is the one network call, at nightly time."""
    url = f"{index}/{package}/json"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def tracked(subject: subjects.Subject) -> list[str]:
    """The packages whose releases the table covers: the subject's own, or else its SDK."""
    if subject.track:
        return [subjects.package_name(t) for t in subject.track]
    sdks = [subjects.package_name(t) for t in subjects.base().get("track", ())]
    return [p for p in sdks if p in subject.providers]


# The names a package carries for the provider whose calls it instruments. Azure calls go through
# the openai SDK, so a package named for OpenAI changes them too.
PROVIDER_NAMES = {
    "gemini": ("gemini", "google-genai", "google-generativeai"),
    "azure": ("azure", "openai"),
}


def scenario_ids(subject: subjects.Subject, package: str) -> list[str]:
    """The scenarios a package can change. A package named for a provider changes only its own."""
    named = [p for p in subject.providers if any(n in package for n in PROVIDER_NAMES.get(p, (p,)))]
    providers = named or list(subject.providers)
    return [sid for sid, sc in scenarios.SCENARIOS.items() if sc.provider in providers]


def suite(subject: subjects.Subject) -> str:
    """A fingerprint of what grades the subject: the grading code, its fixtures, the subject."""
    fixtures = paths.PACKAGE / "fixtures"
    files = [p for p in sorted(paths.PACKAGE.glob("*.py")) if p.stem not in PRESENTATION]
    files += sorted(p for p in fixtures.rglob("*") if p.is_file())
    files += sorted(p for p in subject.directory.rglob("*") if p.is_file())
    files.append(subjects.SUBJECTS / "_base.toml")
    digest = hashlib.sha256()
    for path in files:
        if "__pycache__" in path.parts:
            continue
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()[:16]


def sdk_pins(subject: subjects.Subject) -> dict[str, str]:
    """The provider SDK versions the subject runs with when its own packages vary."""
    sdks = {subjects.package_name(t) for t in subjects.base().get("track", ())}
    pins = {}
    for r in subjects.requirements(subject, False):
        if subjects.package_name(r) in sdks and "==" in r:
            pins[subjects.package_name(r)] = r.split("==", 1)[1]
    return pins


def entry(result: dict[str, Any], version: str, released: str, run: str) -> dict[str, Any]:
    """One version's line: the failing checks per scenario with their bug class, and the gaps.

    A cell the harness could not run is `unknown`. A cell whose setup raised (`subject.instrument`)
    is `setup_failed`: the subject's documented setup does not run on this version, often because
    the version is older than the subject's adapter, so it says nothing about the library's
    telemetry and is not a failing check here. A cell whose grading stopped early for another
    reason (the call hung, no LLM span arrived) has no `span.parented` check. Its other checks
    were not graded, which is not a pass, so `partial` keeps the checks such a cell did grade.
    """
    failing: dict[str, dict[str, str]] = {}
    partial: dict[str, list[str]] = {}
    unknown, setup_failed = [], []
    for sid, cell in sorted(result.get("cells", {}).items()):
        ids = [c["id"] for c in cell["checks"]]
        if "harness" in ids:
            unknown.append(sid)
            continue
        if "subject.instrument" in ids:
            setup_failed.append(sid)
            continue
        if "span.parented" not in ids:
            partial[sid] = sorted(ids)
        bad = {
            c["id"]: (bug.id if (bug := bugclasses.classify(c)) else "")
            for c in cell["checks"]
            if c["verdict"] not in ("pass", "n/a")
        }
        if bad:
            failing[sid] = bad
    return {
        "version": version,
        "released": released,
        "run": run,
        "failing": failing,
        "partial": partial,
        "unknown": unknown,
        "setup_failed": setup_failed,
        "error": (result.get("error") or "")[:300],
    }


def first_fixed(versions: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Per failing check, the first version from which it passes and keeps passing.

    A version that could not run the scenario, or did not grade the check, says nothing either
    way, so it is skipped. A check that fails in the newest version that graded it is not fixed,
    and a check that never failed has no entry.
    """
    seen: dict[tuple[str, str], str] = {}
    for v in versions:
        for sid, bad in v["failing"].items():
            for check, bug in bad.items():
                seen.setdefault((sid, check), bug)
    out = []
    for (sid, check), bug in sorted(seen.items()):
        ran = [
            v
            for v in versions
            if not v["error"]
            and sid not in v["unknown"]
            and sid not in v.get("setup_failed", ())
            and check in v.get("partial", {}).get(sid, (check,))
        ]
        fails = [check in v["failing"].get(sid, {}) for v in ran]
        if not ran or fails[-1] or True not in fails:
            continue
        last = len(fails) - 1 - fails[::-1].index(True)
        out.append(
            {"scenario": sid, "check": check, "bug": bug, "version": ran[last + 1]["version"]}
        )
    return out


def _measure(subject, package, version, released, cache, ids) -> dict[str, Any]:
    doc = runner.run([subject], ids, cache, jobs=1, overrides=(f"{package}=={version}",))
    return entry(doc["subjects"][subject.name], version, released, doc["created"][:10])


def build(
    selected: list[subjects.Subject],
    releases: dict[str, list[tuple[str, str]]],
    cache: Path,
    previous: dict[str, Any] | None = None,
    jobs: int = 4,
    k: int = DEFAULT_RELEASES,
) -> dict[str, Any]:
    """The table for these subjects. An earlier entry with the same suite and version is reused."""
    table: dict[str, Any] = {
        "schema": SCHEMA,
        "spantruth": __version__,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "releases": k,
        # The names, so a reader of the table (check.html) needs no copy of bugclasses.py.
        "classes": {c.id: c.name for c in bugclasses.CLASSES},
        "subjects": {},
    }
    work = []
    for s in selected:
        fingerprint = suite(s)
        pins = {subjects.package_name(r): r for r in subjects.requirements(s, False)}
        packages = {}
        for package in tracked(s):
            before = (previous or {}).get("subjects", {}).get(s.name, {}).get("packages", {})
            old = before.get(package, {})
            reuse = {v["version"]: v for v in old.get("versions", ())}
            if old.get("suite") != fingerprint:
                reuse = {}
            ids = scenario_ids(s, package)
            versions = []
            for version, released in releases.get(package, ()):
                if version in reuse:
                    versions.append(reuse[version])
                else:
                    versions.append(None)
                    work.append((s, package, len(versions) - 1, version, released, ids))
            pin = pins.get(package, "")
            packages[package] = {
                "pinned": pin.split("==", 1)[1] if "==" in pin else "",
                "scenarios": ids,
                "suite": fingerprint,
                "versions": versions,
            }
        table["subjects"][s.name] = {
            "title": s.title,
            "providers": list(s.providers),
            "detect": [subjects.package_name(p) for p in s.detect],
            "detect_with": [subjects.package_name(p) for p in s.detect_with],
            "sdks": sdk_pins(s),
            "packages": packages,
        }
    print(f"history: {len(work)} release(s) to run, the rest reused", file=sys.stderr)
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {
            pool.submit(_measure, s, package, version, released, cache, ids): (s, package, i)
            for s, package, i, version, released, ids in work
        }
        for done, future in enumerate(as_completed(futures), 1):
            s, package, i = futures[future]
            row = future.result()
            table["subjects"][s.name]["packages"][package]["versions"][i] = row
            print(
                f"history: {done}/{len(work)} {s.name} {package} {row['version']} "
                f"({time.monotonic() - start:.0f} s)",
                file=sys.stderr,
            )
    for s in table["subjects"].values():
        for p in s["packages"].values():
            p["first_fixed"] = first_fixed(p["versions"])
    return table


def load(path: Path = TABLE) -> dict[str, Any] | None:
    """The table, or None when there is none. A table of another schema is an error."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        table = json.loads(path.read_text())
    except ValueError as exc:
        raise HistoryError(f"{path}: {exc}") from None
    if not isinstance(table, dict) or table.get("schema") != SCHEMA:
        raise HistoryError(f"{path}: not a version table of schema {SCHEMA}")
    return table


def lookup(table: dict[str, Any], subject: str, package: str, version: str) -> dict | None:
    """The table's entry for one version of one package of a subject, or None."""
    p = table.get("subjects", {}).get(subject, {}).get("packages", {}).get(package, {})
    key = version_key(version)
    for v in p.get("versions", ()):
        if key is not None and version_key(v["version"]) == key:
            return v
    return None


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", required=True, help="where to write the table")
    parser.add_argument(
        "--previous", metavar="FILE", help="an earlier table whose entries are reused when valid"
    )
    parser.add_argument(
        "--last",
        type=int,
        default=DEFAULT_RELEASES,
        metavar="K",
        help=f"run the last K releases of each package (default: {DEFAULT_RELEASES}, at most "
        f"{MAX_RELEASES})",
    )
    parser.add_argument("--subject", action="append", help="a built-in subject name, repeatable")
    parser.add_argument(
        "--cache",
        default=str(paths.cache_dir() / "venvs"),
        help="where to keep the subject environments (default: %(default)s)",
    )
    parser.add_argument("--jobs", type=int, default=4, help="releases to run at the same time")


def main(args: argparse.Namespace) -> int:
    if not 1 <= args.last <= MAX_RELEASES:
        print(f"spantruth: --last takes 1 to {MAX_RELEASES}", file=sys.stderr)
        return 2
    names = args.subject or subjects.all_names()
    try:
        selected = [subjects.load(n) for n in names]
    except subjects.SubjectError as exc:
        print(f"spantruth: {exc}", file=sys.stderr)
        return 2
    selected = [s for s in selected if s.kind == "library" and not subjects.is_path(s.ref)]
    previous = load(Path(args.previous)) if args.previous else None
    packages = sorted({p for s in selected for p in tracked(s)})
    releases = {p: recent(fetch(p), args.last) for p in packages}
    table = build(selected, releases, Path(args.cache), previous, args.jobs, args.last)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n")
    print(f"wrote {out}")
    return 0


# `doctor --fast`: the answer from the shipped table, and a local run only for what it lacks.

FAST_NOTE = (
    "The table runs each library alone, with spantruth's pins for the other packages and the "
    "Anthropic SDK's own tracing off. For your exact set of versions together, run "
    "spantruth doctor without --fast."
)
SETUP_NOTE = "spantruth's setup does not run on this version"


def setup_failed(hit: dict[str, Any]) -> set[str]:
    """The scenarios whose setup raised. A table from before `setup_failed` lists them as failing
    `subject.instrument`, so those count too."""
    old = {sid for sid, checks in hit["failing"].items() if "subject.instrument" in checks}
    return set(hit.get("setup_failed", ())) | old


def _sdk_notes(s: subjects.Subject, versions: dict[str, str | None]) -> list[str]:
    """A line per provider SDK whose version in the project differs from the one the table ran."""
    notes = []
    for sdk, pin in sorted(sdk_pins(s).items()):
        mine = versions.get(sdk)
        if sdk in s.providers and mine and version_key(mine) != version_key(pin):
            notes.append(
                f"The table ran {s.title} with {sdk} {pin}, and your project has {sdk} {mine}."
            )
    return notes


def answer(
    table: dict[str, Any],
    selected: list[subjects.Subject],
    versions: dict[str, str | None],
    ids: list[str],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """The table's answer per subject it covers at the project's versions, the subjects it cannot
    answer, and notes for the reader.

    A subject goes to the local run when the table has no line for its version, the package has no
    pin, the run of that version failed or could not run a scenario, or this spantruth grades the
    subject differently from the one that made the table (a stale table).
    """
    answered, missing, notes = [], [], []
    for s in selected:
        packages = table.get("subjects", {}).get(s.name, {}).get("packages", {})
        stale = [p for p, d in packages.items() if p in versions and d.get("suite") != suite(s)]
        if stale:
            missing.append(s.name)
            notes.append(
                f"The table was made by spantruth {table.get('spantruth')}, which grades "
                f"{s.title} differently from this spantruth {__version__}, so it runs locally."
            )
            continue
        rows = []
        for package, data in packages.items():
            if package not in versions:
                continue
            version = versions[package]
            hit = lookup(table, s.name, package, version) if version else None
            if hit is None or hit["error"] or set(hit["unknown"]) & set(ids):
                rows = None
                break
            setup = setup_failed(hit) & set(ids)
            later = version_key(version)
            rows.append(
                {
                    "package": package,
                    "version": version,
                    "run": hit["run"],
                    "scenarios": len([i for i in data["scenarios"] if i in ids]),
                    "setup_failed": len(setup),
                    "failing": {
                        sid: c
                        for sid, c in sorted(hit["failing"].items())
                        if sid in ids and sid not in setup
                    },
                    "fixed_in": {
                        f"{f['scenario']} {f['check']}": f["version"]
                        for f in data.get("first_fixed", ())
                        if version_key(f["version"]) > later and f["check"] != "subject.instrument"
                    },
                }
            )
        if rows:
            answered.append({"name": s.name, "title": s.title, "packages": rows})
            notes += _sdk_notes(s, versions)
        else:
            missing.append(s.name)
    return answered, missing, notes


def render_answer(
    table: dict[str, Any], answered: list[dict[str, Any]], notes: list[str] = ()
) -> str:
    lines = [
        f"From the version table of {table['created'][:10]} (spantruth {table['spantruth']}), "
        "with no run:",
        "",
    ]
    for s in answered:
        for p in s["packages"]:
            lines.append(f"{s['title']}  ({p['package']} {p['version']})")
            if p["setup_failed"]:
                lines.append(
                    f"  {SETUP_NOTE}: {p['setup_failed']} of {p['scenarios']} scenarios have no "
                    "answer."
                )
            if not p["failing"]:
                if not p["setup_failed"]:
                    lines.append(f"  No problem in {p['scenarios']} scenarios.")
                continue
            lines.append(f"  {len(p['failing'])} of {p['scenarios']} scenarios fail:")
            for sid, checks in p["failing"].items():
                for check, bug in checks.items():
                    name = f"{bug} {bugclasses.BY_ID[bug].name}" if bug in bugclasses.BY_ID else ""
                    fixed = p["fixed_in"].get(f"{sid} {check}")
                    tail = f", fixed in {fixed}" if fixed else ""
                    lines.append(f"    {sid}: {check}{f' ({name})' if name else ''}{tail}")
        lines.append("")
    lines += [*notes, FAST_NOTE]
    return "\n".join(lines)


def fast(directory: Path, out: Path, cache: Path, jobs: int = 4, scenario=None) -> int:
    """`doctor --fast`: answer from the shipped table, and run locally only what it lacks.

    Exit code as doctor's: 3 when spantruth could not run something (here: its setup does not run
    on a version), else 1 on a failing check, else 0.
    """
    from . import doctor

    found = doctor.read_versions(directory)
    selected = doctor.match(found.versions)
    table = load()
    if not selected or table is None:
        if table is None:
            print(
                "spantruth: no version table ships with this install; running doctor",
                file=sys.stderr,
            )
        return doctor.run(directory, out, cache, jobs=jobs, scenario=scenario)
    ids = scenario or doctor.scenario_ids(found.versions)
    answered, missing, notes = answer(table, selected, found.versions, ids)
    print(f"Read {found.source}.")
    if answered:
        print(render_answer(table, answered, notes))
    elif notes:
        print("\n".join(notes))
    rows = [p for s in answered for p in s["packages"]]
    code = (
        3 if any(p["setup_failed"] for p in rows) else 1 if any(p["failing"] for p in rows) else 0
    )
    if not missing:
        return code
    print(f"\nNot in the table at your versions: {', '.join(missing)}. Running them now.\n")
    local = doctor.run(directory, out, cache, jobs=jobs, scenario=scenario, only=missing)
    return 3 if 3 in (code, local) else max(code, local)
