"""`spantruth doctor`: what do the versions in this project get wrong?

It reads the project's versions, runs the built-in subjects it recognizes at those versions, and
explains the findings by bug class. It grades library versions against the fake provider. It never
imports or runs the project's own code, and it never reads anything but the dependency files.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import shutil
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import bugclasses, report, runner, scenarios, subjects

SELF_TRACING = "ANTHROPIC_OPEN_TELEMETRY"
EXPORTER = "opentelemetry-exporter-otlp-proto-http"
GUIDE = "https://github.com/Plexito-de/spantruth/blob/main/docs/test-your-wrapper.md"
UNCOVERED = Path(__file__).with_name("uncovered.toml")
UV_INSTALL = "https://docs.astral.sh/uv/getting-started/installation/"


class DoctorError(ValueError):
    """Nothing to check, or a dependency file that cannot be read. Never a clean result."""


class CannotRun(RuntimeError):
    """A tool that doctor needs is missing, so nothing was graded (exit 3)."""


@dataclass(frozen=True)
class Found:
    source: str  # The file or directory the versions come from.
    versions: dict[str, str | None]  # Normalized name to version. None: the file pins none.


def _toml_lock(path: Path) -> dict[str, str | None]:
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise DoctorError(f"cannot read {_line(path)}: {_line(exc)}") from None
    return {
        subjects.package_name(p["name"]): p.get("version")
        for p in data.get("package", ())
        if "name" in p
    }


def _pipfile_lock(path: Path) -> dict[str, str | None]:
    try:
        data = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise DoctorError(f"cannot read {_line(path)}: {_line(exc)}") from None
    out: dict[str, str | None] = {}
    for section in ("default", "develop"):
        for name, row in data.get(section, {}).items():
            version = row.get("version", "") if isinstance(row, dict) else ""
            out[subjects.package_name(name)] = version.removeprefix("==") or None
    return out


def _requirement(line: str) -> tuple[str, str | None] | None:
    """One requirement line as a name and its pinned version. None for anything else."""
    line = line.split(" #", 1)[0].split(";", 1)[0].strip()
    if not line or line.startswith(("#", "-")):
        return None
    pinned = re.fullmatch(r"([A-Za-z0-9._-]+)(?:\[[^\]]*\])?\s*==\s*([^\s,]+)", line)
    if pinned:
        return subjects.package_name(pinned[1]), pinned[2]
    return subjects.package_name(line), None


def _requirements(paths: list[Path]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for path in paths:
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError) as exc:
            raise DoctorError(f"cannot read {_line(path)}: {_line(exc)}") from None
        # A line ending in a backslash continues on the next, as `--generate-hashes` writes them,
        # and the `--hash` options after a pin are not part of it.
        text = re.sub(r"\\\r?\n", " ", text)
        for line in text.splitlines():
            line = re.sub(r"\s--hash[=\s]\S+", "", line)
            if parsed := _requirement(line):
                out.setdefault(*parsed)
    return out


def _venv(site_packages: list[Path]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for packages in site_packages:
        for info in packages.glob("*.dist-info"):
            meta = info / "METADATA"
            if not meta.is_file():
                continue
            headers = dict(
                line.split(": ", 1)
                for line in meta.read_text(errors="replace").split("\n\n", 1)[0].splitlines()
                if ": " in line
            )
            if "Name" in headers:
                out[subjects.package_name(headers["Name"])] = headers.get("Version")
    return out


def _pyproject(path: Path) -> dict[str, str | None]:
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise DoctorError(f"cannot read {_line(path)}: {_line(exc)}") from None
    parsed = map(_requirement, data.get("project", {}).get("dependencies", ()))
    return dict(p for p in parsed if p)


LOCKS = {
    "uv.lock": _toml_lock,
    "poetry.lock": _toml_lock,
    "pdm.lock": _toml_lock,
    "Pipfile.lock": _pipfile_lock,
}
# Directories whose lock files belong to something other than a subproject of this one.
NOT_SUBPROJECTS = {"node_modules", "site-packages", "__pypackages__"}


def _read_file(path: Path) -> Found:
    """The versions in one dependency file that the caller named.

    A requirements file is one by its name, or by a directory named `requirements`, so that a
    README or a notes file is never read as one.
    """
    name = _line(path.name)
    if path.name in LOCKS:
        return Found(name, LOCKS[path.name](path))
    if path.name == "pyproject.toml":
        return Found(name, _pyproject(path))
    if fnmatch.fnmatch(path.name, "requirements*.txt") or (
        path.suffix == ".txt" and path.parent.name == "requirements"
    ):
        return Found(name, _requirements([path]))
    raise DoctorError(
        f"cannot read versions from {name}: give a lock file, a requirements*.txt file, a "
        "pyproject.toml or a project directory"
    )


def subprojects(directory: Path, depth: int = 3) -> list[tuple[Path, str]]:
    """The directories below this one with a lock file, and its name. A monorepo has several.

    A hidden directory, a virtual environment or installed packages are not a subproject, and
    the search stops at the first lock file on each path.
    """
    out: list[tuple[Path, str]] = []
    try:
        children = sorted(p for p in directory.iterdir() if p.is_dir() and not p.is_symlink())
    except OSError:
        return out
    for child in children:
        if child.name.startswith(".") or child.name in NOT_SUBPROJECTS:
            continue
        if (child / "pyvenv.cfg").is_file():
            continue
        lock = next((n for n in LOCKS if (child / n).is_file()), None)
        if lock:
            out.append((child, lock))
        elif depth > 1:
            out += subprojects(child, depth - 1)
    return out


def _monorepo_error(directory: Path, found: list[tuple[Path, str]]) -> DoctorError:
    # A directory name is anybody's text: no control sequence of it reaches the terminal.
    lines = [
        f"  spantruth doctor {shlex.quote(_line(path))}  ({_line(lock)})" for path, lock in found
    ]
    some = "1 subproject has one" if len(found) == 1 else f"{len(found)} subprojects have one"
    return DoctorError(
        f"no lock file in {_line(directory.resolve())}, but {some}. "
        "Run doctor in each:\n" + "\n".join(lines)
    )


def _is_workspace_root(directory: Path) -> bool:
    """True when the root pyproject.toml grades nothing alone, so its subprojects are the answer.

    That is no pyproject.toml, a uv workspace, or one that names no library doctor runs. A single
    project with a lock file in its test fixtures is read as before.
    """
    path = directory / "pyproject.toml"
    if not path.is_file():
        return True
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False  # Read it as before, and say that it cannot be read.
    if "workspace" in data.get("tool", {}).get("uv", {}):
        return True
    return not match(_pyproject(path))


def read_versions(directory: Path) -> Found:
    """The project's versions, from the most exact source it has: a lock file first.

    `directory` can also be one dependency file. A directory with no lock file, no requirements
    file and no .venv, whose subdirectories have lock files, is a monorepo: its pyproject.toml
    pins nothing, so doctor names the subprojects instead of grading nothing.
    """
    directory = Path(directory)
    if directory.is_file():
        return _read_file(directory)
    if not directory.is_dir():
        raise DoctorError(f"no such file or directory: {_line(directory)}")
    for name, reader in LOCKS.items():
        if (directory / name).is_file():
            return Found(name, reader(directory / name))
    wanted = sorted(directory.glob("requirements*.txt"))
    if wanted:
        return Found(
            wanted[0].name if len(wanted) == 1 else "requirements*.txt", _requirements(wanted)
        )
    packages = sorted(directory.glob(".venv/lib/python*/site-packages"))
    if packages:
        return Found(".venv", _venv(packages))
    if _is_workspace_root(directory) and (found := subprojects(directory)):
        raise _monorepo_error(directory, found)
    if (directory / "pyproject.toml").is_file():
        return Found("pyproject.toml", _pyproject(directory / "pyproject.toml"))
    raise DoctorError(
        f"no lock file, requirements file, .venv or pyproject.toml in {_line(directory.resolve())}"
    )


def _covers_anthropic(subject: subjects.Subject, versions: Mapping[str, str | None]) -> bool:
    if "anthropic" not in subject.providers:
        return False
    named = [
        subjects.package_name(p) for p in subject.detect if subjects.package_name(p) in versions
    ]
    return len(subject.detect) == 1 or any("anthropic" in p for p in named)


def match(versions: Mapping[str, str | None]) -> list[subjects.Subject]:
    """The built-in subjects this project uses. The SDK alone counts if the app has an OTel SDK."""
    every = [subjects.load(n) for n in subjects.all_names()]

    def uses(subject: subjects.Subject) -> bool:
        named = any(subjects.package_name(p) in versions for p in subject.detect)
        needed = not subject.detect_with or any(
            subjects.package_name(p) in versions for p in subject.detect_with
        )
        return subject.kind == "library" and named and needed

    found = [s for s in every if uses(s)]
    if (
        "anthropic" in versions
        and "opentelemetry-sdk" in versions
        and not any(_covers_anthropic(s, versions) for s in found)
    ):
        found.append(subjects.load("anthropic-sdk"))
    return found


def uncovered_table() -> dict[str, dict[str, str]]:
    """Package name to the title of the library and the issue where a user can ask for it."""
    return tomllib.loads(UNCOVERED.read_text())


def uncovered(versions: Mapping[str, str | None]) -> list[dict[str, Any]]:
    """The LLM libraries in this project that no subject covers yet, one row per library.

    A package that a library subject detects is covered, so a new subject needs no edit here.
    """
    covered = {
        subjects.package_name(p)
        for s in map(subjects.load, subjects.all_names())
        if s.kind == "library"
        for p in s.detect
    }
    rows: dict[str, dict[str, Any]] = {}
    for name, row in sorted(uncovered_table().items()):
        if name in versions and name not in covered:
            entry = rows.setdefault(
                row["issue"], {"title": row["title"], "packages": [], "issue": row["issue"]}
            )
            entry["packages"].append(name)
    return sorted(rows.values(), key=lambda r: r["title"])


def _uncovered_lines(versions: Mapping[str, str | None]) -> list[str]:
    rows = uncovered(versions)
    lines = [
        f"Not covered yet: {_line(r['title'])} ({', '.join(r['packages'])}). "
        f"Ask for it at {r['issue']}"
        for r in rows
    ]
    return [*lines, f"For a wrapper of your own, see {GUIDE}"] if lines else []


def overrides(
    selected: list[subjects.Subject], versions: Mapping[str, str | None]
) -> tuple[str, ...]:
    """A `--with` value per package that a selected subject installs and the project pins."""
    installed = {
        subjects.package_name(r) for s in selected for r in subjects.requirements(s, False)
    }
    pins = {n: versions[n] for n in installed if versions.get(n)}
    # The harness exports over OTLP, and that exporter requires the SDK of its own version. A
    # project can pin the SDK alone, or use the gRPC exporter, so the HTTP exporter follows the SDK.
    if "opentelemetry-sdk" in pins and EXPORTER in installed:
        pins.setdefault(EXPORTER, pins["opentelemetry-sdk"])
    return tuple(sorted(f"{name}=={version}" for name, version in pins.items()))


def as_the_app_runs(
    subject: subjects.Subject,
    versions: Mapping[str, str | None],
    environ: Mapping[str, str] = os.environ,
) -> subjects.Subject:
    """The subject with the SDK's own tracing as the app has it: on, unless the caller said off.

    The base file turns it off so that libraries are graded alone. An app does not. The project
    need not name `anthropic`, because a dependency can bring it. What counts is that Anthropic
    scenarios run.
    """
    if not any(scenarios.get(i).provider == "anthropic" for i in scenario_ids(versions)):
        return subject
    return replace(subject, env={**subject.env, SELF_TRACING: environ.get(SELF_TRACING, "true")})


def scenario_ids(versions: Mapping[str, str | None]) -> list[str]:
    """The scenarios of the providers the project uses, or all of them when it names neither."""
    every = list(scenarios.SCENARIOS)
    used = {sc.provider for sc in scenarios.SCENARIOS.values() if sc.provider in versions}
    return [i for i in every if scenarios.get(i).provider in used] if used else every


def _line(value: Any) -> str:
    """A library's value on one terminal line: no newline and no control sequence gets through."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", str(value)).strip()


def _short(value: Any) -> str:
    if value is None:
        return "nothing"
    return ", ".join(map(str, value)) if isinstance(value, (list, tuple)) else str(value)


def _example(f: dict[str, Any]) -> str:
    parts = []
    if f.get("expected") is not None or f.get("got") is not None:
        parts.append(f"expected {_short(f.get('expected'))}, reported {_short(f.get('got'))}")
    if f.get("detail"):
        parts.append(f["detail"])
    text = _line(": ".join(parts))
    return f"{f['scenario']}: {text[:160]}" if text else f["scenario"]


# The command that installs a requirement, per file that doctor reads the versions from.
INSTALL = {
    "uv.lock": "uv add",
    "poetry.lock": "poetry add",
    "pdm.lock": "pdm add",
    "Pipfile.lock": "pipenv install",
}


# doctor reads the pins from these files, so the fix is an edit of the file. An install alone
# would be undone by the next `pip install -r`, and the next doctor run would read the old pin.
EDIT = {
    "requirements*.txt": "your requirements files",
    "pyproject.toml": "pyproject.toml",
}


def _release(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version.split("+")[0])[:4])


def _needed(specs: list[str], installed: Mapping[str, str]) -> list[str]:
    """One spec per package: the highest lower bound, and none that the installed version meets."""
    have = {subjects.package_name(k): v for k, v in installed.items()}
    # In the order of first appearance: a package's lower bound, or a spec of another form as is.
    bounds: dict[str, str | None] = {}
    for spec in specs:
        m = re.fullmatch(r"\s*([A-Za-z0-9._-]+)\s*>=\s*([0-9][^\s,;]*)\s*", spec)
        if not m:
            bounds.setdefault(spec, None)
            continue
        name, low = subjects.package_name(m[1]), m[2]
        if not bounds.get(name) or _release(low) > _release(bounds[name] or ""):
            bounds[name] = low
    return [
        key if low is None else f"{key}>={low}"
        for key, low in bounds.items()
        if low is None or key not in have or _release(have[key]) < _release(low)
    ]


def upgrade_command(
    source: str | None, specs: list[str], installed: Mapping[str, str] | None = None
) -> str | None:
    """What moves the project to the fixed versions, with the project's own tool or file.

    A lock file has its tool. A requirements file or a bare pyproject.toml holds the pin itself,
    so the fix is an edit of that file. A .venv names no tool, so pip it is.
    """
    specs = _needed(specs, installed or {})
    if not specs:
        return None
    quoted = " ".join(map(shlex.quote, specs))
    if source in EDIT or (source or "").endswith(".txt"):
        return f"require {quoted} in {EDIT.get(source or '', source)}"
    return f"{INSTALL.get(source or '', 'pip install')} {quoted}"


def _cost_order(c: dict[str, Any]) -> tuple[bool, float]:
    """Largest cost error first, either sign. A class with no figure comes after, in class order."""
    cost = c["largest_cost_error"]
    return (cost is None, -abs(cost or 0))


def summarize(doc: dict, source: str | None = None) -> list[dict[str, Any]]:
    """Per subject: its versions, and its findings grouped by bug class, largest cost first.

    `source` is the file the versions came from, which picks the upgrade command.
    """
    out = []
    all_found = report.findings(doc)
    for name, result in doc["subjects"].items():
        found = [f for f in all_found if f["subject"] == name]
        classes = []
        for c in bugclasses.CLASSES:
            mine = [f for f in found if f["bug_class"] is c]
            if not mine:
                continue
            costs = [
                f["class_cost"].percent for f in mine if f["class_cost"] and f["class_cost"].percent
            ]
            issues = {f["issue"]["url"]: f["issue"] for f in mine if f["issue"]}
            links = sorted(issues)
            # `fixed_in` is optional in known_issues.toml, and only a fixed issue has a fix to get.
            fixed_in = {u: list(issues[u].get("fixed_in", ())) for u in links}
            specs = [
                s for u in links if issues[u].get("status") == "fixed-upstream" for s in fixed_in[u]
            ]
            classes.append(
                {
                    "id": c.id,
                    "name": c.name,
                    "summary": c.summary,
                    "remedy": c.remedy,
                    "scenarios": len({f["scenario"] for f in mine}),
                    "largest_cost_error": max(costs, key=abs) if costs else None,
                    "example": _example(mine[0]),
                    # The skill reads `upstream` as a list of URLs, so it stays one.
                    "upstream": links,
                    "upstream_issues": [
                        {
                            "url": u,
                            "status": issues[u].get("status", "reported"),
                            "note": issues[u].get("note", ""),
                            "fixed_in": fixed_in[u],
                        }
                        for u in links
                    ],
                    "upgrade": upgrade_command(source, specs, result.get("versions", {})),
                }
            )
        classes.sort(key=_cost_order)
        stuck = [f for f in found if f["verdict"] == "inconclusive"]
        out.append(
            {
                "name": name,
                "title": result["title"],
                "versions": result.get("versions", {}),
                "scenarios": len(result["cells"]),
                "failing": len({f["scenario"] for f in found}),
                "classes": classes,
                "other": sorted({f["id"] for f in found if f["bug_class"] is None}),
                "inconclusive": len({f["scenario"] for f in stuck}),
                "reason": _last_line(stuck[0]["detail"]) if stuck else None,
                "error": result.get("error"),
            }
        )
    return out


def _percent(value: float) -> str:
    return f"{value:+.1f}%".replace(".0%", "%")


def render_text(found: Found, summary: list[dict[str, Any]], results: str) -> str:
    pins = ", ".join(f"{s['name']}" for s in summary)
    lines = [f"Read {found.source}. Checking: {pins}.", ""]
    for s in summary:
        versions = ", ".join(f"{k} {v}" for k, v in sorted(s["versions"].items()))
        lines.append(f"{_line(s['title'])}  ({_line(versions)})")
        if unread := _unread(s["name"], found):
            lines.append(
                f"  {found.source} has no version to apply for {', '.join(unread)}, so this "
                "checks spantruth's own pin."
            )
        if s["error"]:
            lines.append(f"  could not run: {_line(s['error'])[:300]}")
            if s["error"].startswith("environment build failed"):
                python = subjects.base()["python"]
                lines.append(
                    f"    spantruth installs each library in Python {python}, with your pins. "
                    "uv printed the reason above: a pin that has no build for that Python, or "
                    "two pins that do not fit together."
                )
        elif not s["failing"]:
            lines.append(f"  No problem in {s['scenarios']} scenarios.")
        for c in s["classes"]:
            lines.append(f"  {c['id']} {c['name']}: {c['scenarios']} of {s['scenarios']} scenarios")
            if c["upgrade"]:
                # A released fix beats every workaround, so it comes before the explanation.
                lines.append(f"    Fix first: {_line(c['upgrade'])}")
            lines.append(f"    {c['summary']}")
            if c["largest_cost_error"] is not None:
                note = " in a backend that sums across spans" if c["id"] == "ST02" else ""
                lines.append(
                    f"    Cost figure for one call: {_percent(c['largest_cost_error'])}{note}, "
                    "at list price"
                )
            lines.append(f"    For example, {c['example']}")
            if c["remedy"]:
                lines.append(f"    Today: {c['remedy']}")
            lines += [_upstream_line(i) for i in c["upstream_issues"]]
            lines.append(f"    More: {report.BUGS_URL}#{c['id']}")
        if s["other"]:
            lines.append(f"  Other failing checks: {_line(', '.join(s['other']))}")
        if s["inconclusive"]:
            lines.append(
                f"  Could not run {s['inconclusive']} of {s['scenarios']} scenarios: "
                f"{_line(s['reason'])[:200]}"
            )
            if added := _added(s, found):
                lines.append(
                    "    Your project pins no version of these, so they are at spantruth's own "
                    f"versions: {_line(added)}. One of them may need newer pins than yours."
                )
        lines.append("")
    if extra := _uncovered_lines(found.versions):
        lines += [*extra, ""]
    if len(summary) > 1:
        lines += [
            "Each library was checked alone. If two of them trace the same calls in one app, "
            "each call also reports usage twice.",
            "",
        ]
    lines += [
        "This checks your library versions against a local fake provider. It does not run your "
        "application, so it cannot see settings in your code, such as open_telemetry=False, and it "
        "knows nothing about your backend.",
        f"Results: {results}",
        f"Open a report: spantruth report {results} --html report.html",
    ]
    return "\n".join(lines)


def _upstream_line(issue: dict[str, str]) -> str:
    """A fixed-upstream issue says how it was fixed, so the reader knows to upgrade."""
    if issue["status"] == "fixed-upstream":
        parts = ["    Fixed upstream:", _line(issue["note"]), _line(issue["url"])]
        return " ".join(p for p in parts if p)
    return f"    Tracked at {_line(issue['url'])}"


def _last_line(text: str | None) -> str:
    """The last line of a traceback is the exception, which is what a reader needs."""
    rows = [r for r in (text or "").splitlines() if r.strip()]
    return rows[-1].strip() if rows else "no reason given"


def _added(summary: dict[str, Any], found: Found) -> str:
    """The packages a subject installed at spantruth's pin, because the project gave no version."""
    applied = {
        subjects.package_name(spec)
        for spec in overrides([subjects.load(summary["name"])], found.versions)
    }
    return ", ".join(
        f"{name} {version}"
        for name, version in sorted(summary["versions"].items())
        if subjects.package_name(name) not in applied
    )


def _unread(subject_name: str, found: Found) -> list[str]:
    """Packages that say this project uses the library, with no version spantruth could apply."""
    subject = subjects.load(subject_name)
    installed = {subjects.package_name(r) for r in subjects.requirements(subject, False)}
    return sorted(
        name
        for name in map(subjects.package_name, subject.detect)
        if name in found.versions and (name not in installed or not found.versions[name])
    )


def exit_code(doc: dict, summary: list[dict[str, Any]]) -> int:
    if runner.incomplete(doc):
        return 3
    return 1 if any(s["failing"] for s in summary) else 0


def _known_packages() -> str:
    names = [subjects.load(n) for n in subjects.all_names()]
    return ", ".join(sorted(p for s in names if s.kind == "library" for p in s.detect))


def json_doc(
    found: Found, summary: list[dict[str, Any]], results: str | None, code: int
) -> dict[str, Any]:
    """What `--json` prints. `results` is None when nothing ran."""
    return {
        "source": found.source,
        "results": results,
        "exit_code": code,
        "subjects": summary,
        "uncovered": uncovered(found.versions),
    }


def run(
    directory: Path,
    out: Path,
    cache: Path,
    jobs: int = 4,
    scenario: list[str] | None = None,
    as_json: bool = False,
) -> int:
    found = read_versions(directory)
    selected = match(found.versions)
    if not selected:
        if as_json:
            print(json.dumps(json_doc(found, [], None, 2), indent=1))
        if extra := _uncovered_lines(found.versions):
            raise DoctorError(
                f"no library in {found.source} that spantruth covers yet.\n" + "\n".join(extra)
            )
        raise DoctorError(
            f"no instrumentation library in {found.source}. spantruth recognizes "
            f"{_known_packages()}. For a wrapper of your own, see {GUIDE}"
        )
    ids = scenario or scenario_ids(found.versions)
    for sid in ids:
        if sid not in scenarios.SCENARIOS:
            raise DoctorError(f"unknown scenario {sid}; run `spantruth list`")
    if shutil.which("uv") is None:
        raise CannotRun(
            f"doctor needs uv to build an environment per library, and uv is not on PATH. "
            f"Install it: {UV_INSTALL}"
        )
    print(
        f"Read {found.source}. Running {', '.join(s.name for s in selected)}. The first run "
        "builds an environment per library, which takes about a minute.",
        file=sys.stderr,
    )
    doc = runner.run(
        [as_the_app_runs(s, found.versions) for s in selected],
        scenarios.with_dependencies(ids),
        cache,
        jobs=jobs,
        overrides=overrides(selected, found.versions),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    summary = summarize(doc, found.source)
    if as_json:
        print(json.dumps(json_doc(found, summary, str(out), exit_code(doc, summary)), indent=1))
    else:
        print(render_text(found, summary, str(out)))
    return exit_code(doc, summary)
