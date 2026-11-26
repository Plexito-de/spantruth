"""`spantruth doctor --base REF`: what a change of the lock file changes in the findings.

It reads the dependency files at REF with `git show`, runs doctor at both versions, and names each
finding that the change fixes or adds, per library and bug class. The usual case is a Dependabot
pull request that bumps an instrumentation package. Like doctor, it never runs the project's code.
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import doctor, report, runner, scenarios, subjects
from .doctor import CannotRun, DoctorError, Found

MARKER = "doctor"


def _git(args: list[str], cwd: Path) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    except FileNotFoundError:
        raise CannotRun("--base needs git, and git is not on PATH") from None
    if proc.returncode != 0:
        raise DoctorError(f"git {' '.join(args)}: {doctor._line(proc.stderr)[:300]}")
    return proc.stdout


def _wanted(name: str) -> bool:
    return (
        name in doctor.LOCKS
        or name == "pyproject.toml"
        or fnmatch.fnmatch(name, "requirements*.txt")
    )


def read_at(path: Path, ref: str, tmp: Path) -> Found:
    """The project's versions at a git ref: its dependency files copied out with `git show`.

    A ref with none of them, such as the commit before the project had a lock file, has no
    versions, so every finding of today is new.
    """
    path = Path(path)
    where = path.parent if path.is_file() else path
    if not where.is_dir():
        raise DoctorError(f"no such directory: {doctor._line(where)}")
    check = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=where,
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        raise DoctorError(
            f"no commit {doctor._line(ref)} in the git repository of {doctor._line(where)}. "
            f"Fetch it first, such as: git fetch origin {doctor._line(ref)}"
        )
    names = _git(["ls-tree", "--name-only", ref, "--", "."], where).splitlines()
    names = [n for n in names if (n == path.name if path.is_file() else _wanted(n))]
    if not names:
        return Found(f"nothing at {ref}", {})
    for name in names:
        (tmp / name).write_text(_git(["show", f"{ref}:./{name}"], where))
    return doctor.read_versions(tmp / names[0] if path.is_file() else tmp)


def changes(base: list[dict[str, Any]], head: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each bug class that the change fixed or added, per library, in library and class order."""

    def classes(summary: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        return {(s["name"], c["id"]): c for s in summary for c in s["classes"]}

    before, after = classes(base), classes(head)
    out = []
    for key in sorted(before.keys() | after.keys()):
        if (key in before) == (key in after):
            continue
        c = after.get(key) or before[key]
        out.append(
            {
                "subject": key[0],
                "class": key[1],
                "name": c["name"],
                "change": "added" if key in after else "fixed",
                "base_scenarios": before[key]["scenarios"] if key in before else 0,
                "head_scenarios": after[key]["scenarios"] if key in after else 0,
            }
        )
    return out


def version_changes(
    base: Found, head: Found, selected: list[subjects.Subject]
) -> list[tuple[str, str | None, str | None]]:
    """The packages of the libraries in either run whose version the change moved."""
    names = {
        subjects.package_name(p)
        for s in selected
        for p in (*s.detect, *subjects.requirements(s, False))
    }
    return [
        (n, base.versions.get(n), head.versions.get(n))
        for n in sorted(names)
        if base.versions.get(n) != head.versions.get(n)
    ]


def render(
    ref: str,
    base: Found,
    head: Found,
    moved: list[tuple[str, str | None, str | None]],
    found: list[dict[str, Any]],
    incomplete: bool,
) -> str:
    line = doctor._line
    lines = [f"Compared with {line(ref)}: {line(base.source)} there, {line(head.source)} now."]
    for name, old, new in moved:
        lines.append(f"  {line(name)} {line(old or 'none')} -> {line(new or 'none')}")
    if incomplete:
        lines.append("The run is incomplete, so it was not compared.")
    elif not found:
        lines.append("No finding changed.")
    for c in found if not incomplete else ():
        fixed = c["change"] == "fixed"
        n = c["base_scenarios"] if fixed else c["head_scenarios"]
        where = f"{n} scenario{'s' * (n != 1)}"
        lines.append(
            f"Fixed in {c['subject']}: {c['class']} {c['name']} (was in {where})"
            if fixed
            else f"New in {c['subject']}: {c['class']} {c['name']} (in {where})"
        )
    return "\n".join(lines)


def comment(
    head: list[dict[str, Any]],
    ref: str | None,
    moved: list[tuple[str, str | None, str | None]],
    found: list[dict[str, Any]],
    incomplete: bool,
    link: str,
) -> str:
    """A pull request comment, which the action keeps up to date.

    Library names and class IDs are spantruth's own. Refs, package names and versions come from the
    repository, so each is a code span that no backtick in it can close.
    """
    code = report._code
    lines = [report.comment_marker(MARKER), "### spantruth doctor", ""]
    if incomplete:
        lines += ["⚠️ spantruth could not run every library, so the result is incomplete.", ""]
    if ref is not None and not incomplete:
        fixed = sum(c["change"] == "fixed" for c in found)
        added = len(found) - fixed
        noun = "finding" if fixed == 1 else "findings"
        lines.append(
            f"Compared with {code(ref)}: {fixed} {noun} fixed, {added} new."
            if found
            else f"Compared with {code(ref)}: no finding changed."
        )
        if found:
            lines += ["", "| Library | Bug | Change |", "| --- | --- | --- |"]
            for c in found:
                n = c["head_scenarios"]
                change = (
                    "fixed" if c["change"] == "fixed" else f"new, {n} scenario" + "s" * (n != 1)
                )
                bug = report.bug_link(doctor.bugclasses.BY_ID.get(c["class"]))
                lines.append(f"| {code(c['subject'])} | {bug} | {change} |")
    if moved:
        pairs = ", ".join(
            f"{code(n)} {code(old or 'none')} → {code(new or 'none')}" for n, old, new in moved
        )
        lines += ["", f"Versions that changed: {pairs}."]
    now = [
        f"{report.bug_link(doctor.bugclasses.BY_ID[c['id']])} in {code(s['name'])}"
        for s in head
        for c in s["classes"]
    ]
    if now:
        lines += ["", f"Bug classes now: {', '.join(now)}."]
    elif head and not incomplete:
        lines += ["", f"No problem found in {', '.join(code(s['name']) for s in head)}."]
    lines += ["", report.comment_footer(link)]
    return "\n".join(lines) + "\n"


def run(
    directory: Path,
    out: Path,
    cache: Path,
    base: str | None,
    jobs: int = 4,
    scenario: list[str] | None = None,
    as_json: bool = False,
    comment_file: Path | None = None,
    summary_file: Path | None = None,
    link: str = "",
) -> int:
    """doctor, compared with the project at `base` when given, with a comment and a summary."""
    head = doctor.read_versions(directory)
    old = None
    if base:
        with tempfile.TemporaryDirectory() as tmp:
            old = read_at(Path(directory), base, Path(tmp))
    head_sel = doctor.match(head.versions)
    old_sel = doctor.match(old.versions) if old else []
    if not head_sel and not old_sel:
        doctor.nothing_to_check(head, as_json)
    wanted = set(doctor.scenario_ids(head.versions))
    if old:
        wanted |= set(doctor.scenario_ids(old.versions))
    # The same scenarios on both sides, so a change is the versions' and not the selection's.
    ids = doctor.checked_ids(scenario or [i for i in scenarios.SCENARIOS if i in wanted])
    head_doc = doctor.grade(head, head_sel, ids, cache, jobs)
    old_doc = None
    if old is not None:
        same = doctor.plan(old, old_sel) == doctor.plan(head, head_sel)
        old_doc = head_doc if same else doctor.grade(old, old_sel, ids, cache, jobs)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(head_doc, indent=1, sort_keys=True) + "\n")
    head_sum = doctor.summarize(head_doc, head.source)
    incomplete = bool(runner.incomplete(head_doc))
    found: list[dict[str, Any]] = []
    moved: list[tuple[str, str | None, str | None]] = []
    base_out = out.with_name(f"{out.stem}-base{out.suffix}")
    if old is not None and old_doc is not None:
        base_out.write_text(json.dumps(old_doc, indent=1, sort_keys=True) + "\n")
        old_sum = doctor.summarize(old_doc, old.source)
        incomplete = incomplete or bool(runner.incomplete(old_doc))
        found = changes(old_sum, head_sum)
        moved = version_changes(old, head, [*head_sel, *old_sel])
    if incomplete:
        code = 3
    elif old is not None:
        code = 1 if any(c["change"] == "added" for c in found) else 0
    else:
        code = doctor.exit_code(head_doc, head_sum)
    body = comment(head_sum, base, moved, found, incomplete, link)
    if comment_file:
        comment_file.write_text(body)
    if summary_file:
        with summary_file.open("a") as f:
            f.write(body + "\n")
    if as_json:
        doc = doctor.json_doc(head, head_sum, str(out), code)
        if old is not None:
            doc["base"] = {"ref": base, "source": old.source, "results": str(base_out)}
            doc["changes"] = found
            doc["versions_changed"] = [{"package": n, "base": a, "head": b} for n, a, b in moved]
        print(json.dumps(doc, indent=1))
    else:
        text = doctor.render_text(head, head_sum, str(out))
        if old is not None:
            text = render(base or "", old, head, moved, found, incomplete) + "\n\n" + text
        print(text)
    return code
