"""Put each receipt's numbers side by side: the wire, the span, and each backend.

    python3 -I summarize.py TAG CELLS_DIR BACKENDS.json PRICES.toml OUT.json

The wire value is the scenario's truth: what the fake provider sent, as spantruth grades it.
The list cost is that truth at the rates in spantruth's prices.toml. Nothing here is typed in:
every number comes from a cell file or from a backend's API answer.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

RECEIPTS = {
    "ghost": ("ST01", "Ghost span"),
    "double": ("ST02", "Double span"),
    "cache": ("ST03", "Cache-blind tokens"),
    "phantom": ("ST04", "Phantom tokens"),
    "vanish": ("ST05", "Vanishing tokens"),
    "falseok": ("ST07", "False OK"),
}
LIBRARY = {
    "openllmetry": "opentelemetry-instrumentation-",
    "otel-official-sdk-on": "opentelemetry-instrumentation-genai-anthropic",
    "openlit": "openlit",
}


def _cost(truth: dict, prices: dict) -> float | None:
    """List price of the call: input counts include cached tokens, as spantruth's truth does."""
    rates = prices["models"].get(truth["request_model"])
    if rates is None or truth["input_tokens"] is None:
        return None
    read, write = truth["cache_read_tokens"] or 0, truth["cache_write_tokens"] or 0
    uncached = truth["input_tokens"] - read - write
    total = uncached * rates["input"] + read * rates["cache_read"] + write * rates["cache_write"]
    total += (truth["output_tokens"] or 0) * rates["output"]
    return round(total / 1e6, 10)


def _versions(cell: dict) -> dict:
    prefix = LIBRARY[cell["subject"]]
    keep = {k: v for k, v in cell["versions"].items() if k.startswith(prefix)}
    keep.update({k: cell["versions"][k] for k in ("anthropic", "openai") if k in cell["versions"]})
    return keep


def _span_usage(cell: dict) -> list[dict]:
    return [
        {
            "name": s["name"],
            "status": s["status"],
            "usage": {k: v for k, v in s["attributes"].items() if k.startswith("gen_ai.usage.")},
        }
        for s in cell["spans"]
        if s["name"] != "spantruth.scenario"
    ]


def main(tag: str, cells_dir: str, backends_path: str, prices_path: str, out: str) -> int:
    cells, backends = Path(cells_dir), json.loads(Path(backends_path).read_text())
    prices = tomllib.loads(Path(prices_path).read_text())
    doc = []
    for key, (cid, cname) in RECEIPTS.items():
        cell = json.loads((cells / f"{tag}-{key}-phoenix.json").read_text())
        phx = backends["phoenix"][f"{tag}-{key}-phoenix"]
        mlf = backends["mlflow"][f"{tag}-{key}-mlflow"]["traces"]
        jgr = backends["jaeger"][f"{tag}-{key}-jaeger"]["spans"]
        doc.append(
            {
                "receipt": key,
                "class": cid,
                "class_name": cname,
                "subject": cell["subject"],
                "versions": _versions(cell),
                "scenario": cell["scenario"],
                "failing_checks": [
                    {k: c[k] for k in ("id", "verdict", "expected", "got", "detail")}
                    for c in cell["checks"]
                    if c["verdict"] not in ("pass", "n/a")
                ],
                "app_exception": cell["app_exception"],
                "wire": {k: v for k, v in cell["truth"].items() if v not in (None, [], False)},
                "list_cost_usd": _cost(cell["truth"], prices),
                "spans": _span_usage(cell),
                "phoenix": {
                    "traces": phx["traceCount"],
                    "tokens_total": phx["tokenCountTotal"],
                    "tokens_prompt": phx["tokenCountPrompt"],
                    "tokens_completion": phx["tokenCountCompletion"],
                    "cost_total": phx["costSummary"]["total"]["cost"],
                    "span_status": {s["name"]: s["statusCode"] for s in phx["spans"]},
                },
                "mlflow": [{"state": t["state"], "token_usage": t["token_usage"]} for t in mlf],
                "jaeger": [{"name": s["name"], "tags": s["tags"]} for s in jgr],
            }
        )
    Path(out).write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    for r in doc:
        print(
            r["class"],
            r["receipt"],
            "list $",
            r["list_cost_usd"],
            "| phoenix",
            r["phoenix"]["tokens_total"],
            r["phoenix"]["cost_total"],
            "| mlflow",
            [(m["state"], (m["token_usage"] or {}).get("total_tokens")) for m in r["mlflow"]],
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:6]))
