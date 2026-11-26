"""Run one spantruth cell and send its spans to a real backend as well as to the grader.

    uv run python cell.py SUBJECT SCENARIO BACKEND PROJECT OUT_DIR

BACKEND is phoenix, mlflow or jaeger. The driver is spantruth's own (`spantruth.driver
instrumented`), in the subject's own environment, against spantruth's fake provider, exactly as
`spantruth run` runs a cell. The only change: the driver's OTLP URL is a tee on loopback. The tee
keeps each request for the grader, and sends the same bytes to the backend.

It writes OUT_DIR/<project>.json: the graded checks, every span as spantruth decoded it, the
requests the fake provider saw, and the backend's answer to each forwarded request.
"""

from __future__ import annotations

import gzip
import json
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from spantruth import checks, paths, runner, scenarios, subjects
from spantruth.mockserver import MockServer
from spantruth.receiver import Receiver, decode_traces

# Loopback only. MLflow takes the experiment from a header, Phoenix the project from the resource.
BACKENDS = {
    "phoenix": "http://127.0.0.1:16106/v1/traces",
    "mlflow": "http://127.0.0.1:15100/v1/traces",
    "jaeger": "http://127.0.0.1:14418/v1/traces",
}


class Tee(Receiver):
    def __init__(self, target: str, headers: dict[str, str]) -> None:
        super().__init__()
        self.target, self.extra = target, headers
        self.forwarded: list[dict] = []
        self.RequestHandlerClass = _TeeHandler


class _TeeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: Tee

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        payload = gzip.decompress(raw) if self.headers.get("content-encoding") == "gzip" else raw
        if self.path == "/v1/traces":
            self.server.add_spans(decode_traces(payload))
            req = urllib.request.Request(
                self.server.target,
                data=payload,
                method="POST",
                headers={"content-type": "application/x-protobuf"} | self.server.extra,
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(req, timeout=10) as resp:
                    status = resp.status
            except urllib.error.HTTPError as exc:
                status = exc.code
            self.server.forwarded.append({"path": self.path, "backend_status": status})
        # Any other path (a library's own export, logs, metrics) is answered and dropped, as the
        # harness receiver does.
        self.send_response(200)
        self.send_header("content-type", "application/x-protobuf")
        self.send_header("content-length", "0")
        self.end_headers()


def _experiment(name: str) -> str:
    """The ID of the MLflow experiment with this name, created if it is new."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        "http://127.0.0.1:15100/api/2.0/mlflow/experiments/create",
        data=json.dumps({"name": name}).encode(),
        headers={"content-type": "application/json"},
    )
    try:
        with opener.open(req, timeout=10) as r:
            return json.load(r)["experiment_id"]
    except urllib.error.HTTPError:
        url = "http://127.0.0.1:15100/api/2.0/mlflow/experiments/get-by-name?experiment_name="
        with opener.open(url + urllib.parse.quote(name), timeout=10) as r:
            return json.load(r)["experiment"]["experiment_id"]


def main(subject_name: str, sid: str, backend: str, project: str, out_dir: str) -> int:
    subject = subjects.load(subject_name)
    sc = scenarios.get(sid)
    cache = paths.cache_dir() / "venvs"
    py, versions = subjects.environment(subject, cache)
    headers = {"x-mlflow-experiment-id": _experiment(project)} if backend == "mlflow" else {}
    with tempfile.TemporaryDirectory() as tmp, MockServer() as mock:
        shim = runner.driver_path(Path(tmp) / "shim")
        base_file, obs_file = Path(tmp) / "baseline.json", Path(tmp) / "obs.json"
        runner._driver(
            py,
            ["baseline", sid, "--mock", mock.url, "--out", str(base_file)],
            runner._env(subject, "http://127.0.0.1:9", shim),
            Path(tmp),
        )
        mock.reset()
        with Tee(BACKENDS[backend], headers) as tee:
            env = runner._env(subject, tee.url, shim)
            # Phoenix files a span under this project. The other backends ignore it.
            env["OTEL_RESOURCE_ATTRIBUTES"] = f"openinference.project.name={project}"
            env["OTEL_SERVICE_NAME"] = project
            proc = runner._driver(
                py,
                [
                    "instrumented",
                    sid,
                    "--mock",
                    mock.url,
                    "--otlp",
                    tee.url,
                    "--subject",
                    str(subject.directory),
                    "--out",
                    str(obs_file),
                ],
                env,
                Path(tmp),
            )
            spans = list(tee.spans)
            forwarded = list(tee.forwarded)
        provider_requests = [{"scenario": s, "path": p} for s, p, _ in mock.requests]
        base = json.loads(base_file.read_text()).get(sid)
        obs = json.loads(obs_file.read_text()) if obs_file.exists() else None
    result = checks.grade(sc, scenarios.truth(sc), base, obs, spans)
    keep = {
        k: v
        for k, v in versions.items()
        if k in {subjects.package_name(r) for r in subjects.requirements(subject, False)}
    }
    doc = {
        "project": project,
        "backend": backend,
        "subject": subject_name,
        "versions": keep,
        "scenario": sid,
        "truth": scenarios.truth(sc).__dict__,
        "verdict": checks.cell_verdict(result),
        "checks": [c.as_dict() for c in result],
        "spans": [
            {
                "name": s.name,
                "scope": s.scope,
                "trace_id": s.trace_id,
                "span_id": s.span_id,
                "parent_span_id": s.parent_span_id,
                "status": s.status,
                "attributes": s.attributes,
            }
            for s in spans
        ],
        "forwarded": forwarded,
        "provider_requests": provider_requests,
        "app_exception": (obs or {}).get("exception"),
        "app_seen": (obs or {}).get("seen"),
        "driver_returncode": proc.returncode,
        "driver_stderr": proc.stderr[-1500:],
    }
    out = Path(out_dir) / f"{project}.json"
    out.write_text(json.dumps(doc, indent=2, sort_keys=True, default=list))
    bad = [f"{c['id']}={c['verdict']}" for c in doc["checks"] if c["verdict"] != "pass"]
    print(
        project,
        doc["verdict"],
        "spans:",
        len(spans),
        "forwarded:",
        [f["backend_status"] for f in forwarded],
        bad,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:6]))
