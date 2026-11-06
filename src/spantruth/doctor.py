"""`spantruth doctor`: what do the versions in this project get wrong?

It reads the project's versions, runs the built-in subjects it recognizes at those versions, and
explains the findings by bug class. It grades library versions against the fake provider. It never
imports or runs the project's own code, and it never reads anything but the dependency files.
"""

from __future__ import annotations

import json
import os
import re
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


class DoctorError(ValueError):
    """Nothing to check, or a dependency file that cannot be read. Never a clean result."""


@dataclass(frozen=True)
class Found:
    source: str  # The file or directory the versions come from.
    versions: dict[str, str | None]  # Normalized name to version. None: the file pins none.


def _toml_lock(path: Path) -> dict[str, str | None]:
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise DoctorError(f"cannot read {path}: {exc}") from None
    return {
        subjects.package_name(p["name"]): p.get("version")
        for p in data.get("package", ())
        if "name" in p
    }


def _pipfile_lock(path: Path) -> dict[str, str | None]:
    try:
        data = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise DoctorError(f"cannot read {path}: {exc}") from None
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
            raise DoctorError(f"cannot read {path}: {exc}") from None
        for line in text.splitlines():
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
        raise DoctorError(f"cannot read {path}: {exc}") from None
    parsed = map(_requirement, data.get("project", {}).get("dependencies", ()))
    return dict(p for p in parsed if p)


def read_versions(directory: Path) -> Found:
    """The project's versions, from the most exact source it has: a lock file first."""
    directory = Path(directory)
    for name, reader in (
        ("uv.lock", _toml_lock),
        ("poetry.lock", _toml_lock),
        ("pdm.lock", _toml_lock),
        ("Pipfile.lock", _pipfile_lock),
    ):
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
    if (directory / "pyproject.toml").is_file():
        return Found("pyproject.toml", _pyproject(directory / "pyproject.toml"))
    raise DoctorError(
        f"no lock file, requirements file, .venv or pyproject.toml in {directory.resolve()}"
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


def summarize(doc: dict) -> list[dict[str, Any]]:
    """Per subject: its versions, and its findings grouped by bug class, in class order."""
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
            links = sorted({f["issue"]["url"] for f in mine if f["issue"]})
            classes.append(
                {
                    "id": c.id,
                    "name": c.name,
                    "summary": c.summary,
                    "remedy": c.remedy,
                    "scenarios": len({f["scenario"] for f in mine}),
                    "largest_cost_error": max(costs, key=abs) if costs else None,
                    "example": _example(mine[0]),
                    "upstream": links,
                }
            )
        out.append(
            {
                "name": name,
                "title": result["title"],
                "versions": result.get("versions", {}),
                "scenarios": len(result["cells"]),
                "failing": len({f["scenario"] for f in found}),
                "classes": classes,
                "other": sorted({f["id"] for f in found if f["bug_class"] is None}),
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
        elif not s["failing"]:
            lines.append(f"  No problem in {s['scenarios']} scenarios.")
        for c in s["classes"]:
            lines.append(f"  {c['id']} {c['name']}: {c['scenarios']} of {s['scenarios']} scenarios")
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
            lines += [f"    Tracked at {u}" for u in c["upstream"]]
            lines.append(f"    More: {report.BUGS_URL}#{c['id']}")
        if s["other"]:
            lines.append(f"  Other failing checks: {_line(', '.join(s['other']))}")
        lines.append("")
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
        raise DoctorError(
            f"no instrumentation library in {found.source}. spantruth recognizes "
            f"{_known_packages()}. For a wrapper of your own, see {GUIDE}"
        )
    ids = scenario or scenario_ids(found.versions)
    for sid in ids:
        if sid not in scenarios.SCENARIOS:
            raise DoctorError(f"unknown scenario {sid}; run `spantruth list`")
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
    summary = summarize(doc)
    if as_json:
        print(
            json.dumps(
                {
                    "source": found.source,
                    "results": str(out),
                    "exit_code": exit_code(doc, summary),
                    "subjects": summary,
                },
                indent=1,
            )
        )
    else:
        print(render_text(found, summary, str(out)))
    return exit_code(doc, summary)
