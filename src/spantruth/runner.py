"""Run subjects against scenarios and write one results document."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__, checks, paths, scenarios, subjects
from .conventions import SEMCONV_GENAI_COMMIT
from .mockserver import MockServer
from .receiver import Receiver

CELL_TIMEOUT = 60
SCHEMA = 1
# A subject that tries to reach the internet through an HTTP client that honors the proxy
# variables fails fast. 127.0.0.1 stays reachable.
OFFLINE = {
    "HTTP_PROXY": "http://127.0.0.1:9",
    "HTTPS_PROXY": "http://127.0.0.1:9",
    "NO_PROXY": "127.0.0.1,localhost",
    "no_proxy": "127.0.0.1,localhost",
}


def driver_path(directory: Path) -> Path:
    """Make a directory that holds only the running spantruth package, for PYTHONPATH.

    A subject environment then runs the driver of this spantruth, from a clone or from a wheel,
    and imports nothing else from the host environment.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "spantruth").symlink_to(paths.PACKAGE, target_is_directory=True)
    return directory


def _env(
    subject: subjects.Subject, otlp: str, shim: Path, content: dict[str, str] | None = None
) -> dict[str, str]:
    keep = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "TMPDIR")}
    return (
        keep
        | OFFLINE
        | {
            "OTEL_EXPORTER_OTLP_ENDPOINT": otlp,
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_SERVICE_NAME": "spantruth",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(shim),
        }
        | subjects.base().get("env", {})
        | subject.env
        | (content or {})
    )


def _driver(
    py: Path, args: list[str], env: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess:
    # -P and a scratch working directory: nothing in the caller's directory can shadow a module.
    return subprocess.run(
        [str(py), "-P", "-m", "spantruth.driver", *args],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=CELL_TIMEOUT,
    )


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def run_subject(
    subject: subjects.Subject,
    ids: list[str],
    mock: MockServer,
    cache: Path,
    latest: bool,
    shim: Path,
    overrides: tuple[str, ...] = (),
) -> dict[str, Any]:
    ids = [i for i in ids if subject.supports(i)]
    out: dict[str, Any] = {
        "title": subject.title,
        "homepage": subject.homepage,
        "kind": subject.kind,
        "notes": subject.notes,
        "ref": subject.ref,
        "cells": {},
    }
    if subject.content:
        out["content"] = {k: subject.content[k] for k in ("switch", "docs", "default")}
    if subject.metrics_docs:
        out["metrics"] = {"docs": subject.metrics_docs}
    if not ids:
        return out
    try:
        py, versions = subjects.environment(subject, cache, latest, overrides)
    except subprocess.CalledProcessError as exc:
        out["error"] = f"environment build failed: {exc}"
        return out
    out["versions"] = {
        k: v
        for k, v in versions.items()
        if subjects.package_name(k)
        in {subjects.package_name(r) for r in subjects.requirements(subject, False)}
    }
    with tempfile.TemporaryDirectory() as tmp:
        base_file = Path(tmp) / "baseline.json"
        try:
            proc = _driver(
                py,
                baseline_args(subject, ids, mock.url, base_file),
                _env(subject, "http://127.0.0.1:9", shim),
                Path(tmp),
            )
            if proc.returncode != 0:
                out["error"] = f"baseline failed: {proc.stderr[-2000:]}"
        except subprocess.TimeoutExpired:
            out["error"] = "baseline timed out"
        baselines = _read(base_file) or {}
        for sid in ids:
            out["cells"][sid] = _cell(subject, sid, py, mock, Path(tmp), shim, baselines.get(sid))
    _downgrade_unsupported_streams(out["cells"])
    return out


def baseline_args(subject: subjects.Subject, ids: list[str], mock: str, out: Path) -> list[str]:
    """The driver's baseline command. Only a subject with an app.py names its directory."""
    args = ["baseline", *ids, "--mock", mock, "--out", str(out)]
    return [*args, "--subject", str(subject.directory)] if subject.app else args


def _cell(subject, sid, py, mock, tmp: Path, shim: Path, base: dict | None) -> dict[str, Any]:
    sc = scenarios.get(sid)
    obs_file = tmp / f"{sc.key}.json"
    stderr = ""
    # A content scenario sets the library's own switch. Without one, the scenario runs as is.
    content = subject.content_env(sc.content) if sc.content else None
    with Receiver() as rx:
        try:
            proc = _driver(
                py,
                [
                    "instrumented",
                    sid,
                    "--mock",
                    mock.url,
                    "--otlp",
                    rx.url,
                    "--subject",
                    str(subject.directory),
                    "--out",
                    str(obs_file),
                ],
                _env(subject, rx.url, shim, content),
                tmp,
            )
            stderr = proc.stderr[-2000:]
        except subprocess.TimeoutExpired:
            stderr = "the cell timed out"
        spans, logs, metrics = list(rx.spans), list(rx.logs), list(rx.metrics)
    obs = _read(obs_file)
    switch = subject.content.get("switch") if content is not None else None
    t = scenarios.truth(sc)
    documented = subject.metrics_docs or None
    result = checks.grade(
        sc, t, base, obs, spans, logs, switch, metrics=metrics, metrics_docs=documented
    )
    return {
        "verdict": checks.cell_verdict(result),
        "checks": [c.as_dict() for c in result],
        "spans": [
            {"name": s.name, "scope": s.scope, "status": s.status, "attributes": s.attributes}
            for s in spans
        ],
        **({"logs": [asdict(rec) for rec in logs]} if logs else {}),
        **({"metrics": [asdict(p) for p in metrics]} if metrics else {}),
        "stderr": stderr if (obs is None or "instrument_error" in (obs or {})) else "",
    }


def _downgrade_unsupported_streams(cells: dict[str, dict]) -> None:
    """A library that records no span for a full stream does not support streaming.

    Its silence on an early exit is then the same gap, not a span lost by the exit, so the
    early-exit cell is `missing`, not `lost`.
    """
    for sid, cell in cells.items():
        stream = cells.get(scenarios.depends_on(sid) or "")
        if stream and any(
            c["id"] == "span.recorded" and c["verdict"] == "missing" for c in stream["checks"]
        ):
            for c in cell["checks"]:
                if c["verdict"] == "lost":
                    c["verdict"], c["detail"] = "missing", "streaming is not instrumented"
            cell["verdict"] = max((c["verdict"] for c in cell["checks"]), key=checks.SEVERITY.index)


def incomplete(doc: dict[str, Any]) -> list[str]:
    """What the harness could not run: an environment, a baseline, or a cell with no observation.

    A setup that raised (`subject.instrument`) is not in the list. It is a verdict on the subject.
    """
    gaps = []
    for name, result in doc["subjects"].items():
        if result.get("error"):
            gaps.append(f"{name}: {result['error'][:300]}")
        for sid, cell in result["cells"].items():
            if any(c["id"] == "harness" for c in cell["checks"]):
                gaps.append(f"{name} {sid}: no observation was written")
    return gaps


def run(
    selected: list[subjects.Subject],
    ids: list[str],
    cache: Path,
    latest: bool = False,
    jobs: int = 4,
    overrides: tuple[str, ...] = (),
) -> dict[str, Any]:
    cache.mkdir(parents=True, exist_ok=True)
    doc: dict[str, Any] = {
        # The version of this document's shape. Raise it when a field changes meaning or goes away.
        "schema": SCHEMA,
        "spantruth": __version__,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "mode": "latest" if latest else "pinned",
        "python": subjects.base()["python"],
        "semconv_genai": SEMCONV_GENAI_COMMIT,
        "scenarios": {
            sid: {
                "title": s.title,
                "catches": s.catches,
                "provider": s.provider,
                "truth": asdict(scenarios.truth(s)),
            }
            for sid, s in scenarios.SCENARIOS.items()
            if sid in ids
        },
        "subjects": {},
    }
    if overrides:
        doc["overrides"] = list(overrides)
    with (
        tempfile.TemporaryDirectory() as tmp,
        MockServer() as mock,
        ThreadPoolExecutor(max_workers=jobs) as pool,
    ):
        shim = driver_path(Path(tmp))
        futures = {
            s.name: pool.submit(run_subject, s, ids, mock, cache, latest, shim, overrides)
            for s in selected
        }
        doc["subjects"] = {n: f.result() for n, f in futures.items()}
    return doc
