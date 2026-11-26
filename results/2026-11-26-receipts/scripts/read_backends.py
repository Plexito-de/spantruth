"""Read what each backend stores for each receipt project, from its own API, on loopback.

    python3 -I read_backends.py PREFIX CELLS_DIR OUT.json

PREFIX selects the projects (Phoenix projects, MLflow experiments) by name. Jaeger traces are
read by the trace IDs in the cell files that cell.py wrote to CELLS_DIR.
Writes one JSON document: backend -> project -> what that backend's API returned.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

PHOENIX = "http://127.0.0.1:16106/graphql"
MLFLOW = "http://127.0.0.1:15100"
JAEGER = "http://127.0.0.1:16786"
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post(url: str, body: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"content-type": "application/json"}
    )
    with NO_PROXY.open(req, timeout=20) as r:
        return json.load(r)


def _get(url: str) -> dict:
    with NO_PROXY.open(url, timeout=20) as r:
        return json.load(r)


PHOENIX_Q = """{ projects { edges { node { name traceCount tokenCountTotal tokenCountPrompt
tokenCountCompletion costSummary { total { cost } prompt { cost } completion { cost } }
spans(first: 50) { edges { node { name spanId parentId statusCode statusMessage tokenCountTotal
tokenCountPrompt tokenCountCompletion costSummary { total { cost } } } } } } } } }"""


def phoenix(prefix: str) -> dict:
    data = _post(PHOENIX, {"query": PHOENIX_Q})["data"]["projects"]["edges"]
    out = {}
    for e in data:
        n = e["node"]
        if n["name"].startswith(prefix):
            n["spans"] = [s["node"] for s in n["spans"]["edges"]]
            out[n["name"]] = n
    return out


def mlflow(prefix: str) -> dict:
    exps = _post(f"{MLFLOW}/api/2.0/mlflow/experiments/search", {"max_results": 1000})
    out = {}
    for x in exps.get("experiments", []):
        if not x["name"].startswith(prefix):
            continue
        loc = {
            "type": "MLFLOW_EXPERIMENT",
            "mlflow_experiment": {"experiment_id": x["experiment_id"]},
        }
        traces = _post(f"{MLFLOW}/api/3.0/mlflow/traces/search", {"locations": [loc]}).get(
            "traces", []
        )
        for t in traces:
            t["token_usage"] = json.loads(
                t.get("trace_metadata", {}).get("mlflow.trace.tokenUsage", "null")
            )
        out[x["name"]] = {"experiment_id": x["experiment_id"], "traces": traces}
    return out


def jaeger(prefix: str, cells: str) -> dict:
    """Jaeger groups by service, and every cell has service.name spantruth: read by trace ID."""
    out = {}
    for path in sorted(Path(cells).glob(f"{prefix}*.json")):
        cell = json.loads(path.read_text())
        if cell["backend"] != "jaeger":
            continue
        spans = []
        for tid in sorted({s["trace_id"] for s in cell["spans"]}):
            for t in _get(f"{JAEGER}/api/traces/{tid}").get("data") or []:
                for s in t["spans"]:
                    tags = {x["key"]: x["value"] for x in s["tags"]}
                    keep = {k: v for k, v in tags.items() if "token" in k or k in JAEGER_TAGS}
                    spans.append({"trace_id": s["traceID"], "name": s["operationName"], "tags": keep})
        out[cell["project"]] = {"spans": spans}
    return out


JAEGER_TAGS = ("error", "otel.status_code", "otel.status_description", "error.type")


def main(prefix: str, cells: str, path: str) -> int:
    doc = {}
    readers = (
        ("phoenix", phoenix),
        ("mlflow", mlflow),
        ("jaeger", lambda p: jaeger(p, cells)),
    )
    for name, fn in readers:
        try:
            doc[name] = fn(prefix)
        except OSError as exc:
            doc[name] = {"error": str(exc)}
    with open(path, "w") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
