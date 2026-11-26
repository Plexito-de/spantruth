"""Write the screenshot jobs for shoot.py: the page in each backend that shows each receipt.

    python3 -I make_jobs.py TAG CELLS_DIR BACKENDS.json OUT.json

BACKENDS.json is what read_backends.py wrote. Phoenix project IDs come from its GraphQL API.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
RECEIPTS = ("phantom", "ghost", "falseok", "double", "cache", "vanish")
# The span that carries the call, in each receipt where one arrives. Its row is clicked.
LLM_SPAN = {
    "phantom": "anthropic.chat",
    "falseok": "openai.chat",
    "double": "anthropic.messages.create",
    "cache": "chat claude-sonnet-5-5",
    "vanish": "openai.chat",
}


def _phoenix_ids() -> dict[str, str]:
    q = json.dumps({"query": "{ projects { edges { node { id name } } } }"}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:16106/graphql", data=q, headers={"content-type": "application/json"}
    )
    with NO_PROXY.open(req, timeout=20) as r:
        return {
            e["node"]["name"]: e["node"]["id"] for e in json.load(r)["data"]["projects"]["edges"]
        }


def _trace(cells: Path, project: str) -> str:
    cell = json.loads((cells / f"{project}.json").read_text())
    return next(s["trace_id"] for s in cell["spans"] if s["name"] == "spantruth.scenario")


def main(tag: str, cells_dir: str, backends_path: str, out: str) -> int:
    cells, backends = Path(cells_dir), json.loads(Path(backends_path).read_text())
    ids = _phoenix_ids()
    jobs = []
    for r in RECEIPTS:
        p, m, j = f"{tag}-{r}-phoenix", f"{tag}-{r}-mlflow", f"{tag}-{r}-jaeger"
        span = LLM_SPAN.get(r)
        # The library's span is selected. On the second page, the mouse rests on the trace's
        # Total Cost, which shows the exact cost in a tooltip.
        steps = [{"click": span}] if span else []
        url = f"http://127.0.0.1:16106/projects/{ids[p]}/traces/{_trace(cells, p)}"
        jobs.append({"name": f"{r}-phoenix-trace", "url": url, "width": 1600, "steps": steps})
        if span:
            cost = [*steps, {"move": [523, 136]}]
            jobs.append({"name": f"{r}-phoenix-cost", "url": url, "width": 1600, "steps": cost})
        exp = backends["mlflow"][m]["experiment_id"]
        jobs.append(
            {
                "name": f"{r}-mlflow-traces",
                "url": f"http://127.0.0.1:15100/#/experiments/{exp}/traces",
                "width": 1600,
                "steps": [
                    {"click": "Got it", "optional": True},
                    {"css": "button[aria-label='Close']", "optional": True},
                ],
            }
        )
        url = f"http://127.0.0.1:16786/trace/{_trace(cells, j)}"
        if not span:
            jobs.append({"name": f"{r}-jaeger-trace", "url": url, "full": True})
            continue
        # The second row is the library's span. Its GenAI tab sums the tokens, and its Details
        # tab lists the attributes with the status.
        for tab, more in (("GenAI", []), ("Details", [{"css": "text=Attributes:"}])):
            jobs.append(
                {
                    "name": f"{r}-jaeger-{tab.lower()}",
                    "url": url,
                    "steps": [{"xy": [800, 294]}, {"click": tab}, *more],
                    "full": True,
                }
            )
    Path(out).write_text(json.dumps(jobs, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:5]))
