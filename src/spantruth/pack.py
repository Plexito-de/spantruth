"""The scenario pack: every scenario as plain data, for a driver or a harness in another language.

`spantruth export` writes it. Each scenario holds the call that the app makes (the call plan), the
responses that the fake provider sends (a JSON body, or a stream of server-sent events), and the
truth: the values that a correct instrumentation reports. Nothing in it needs Python to read.

`FORMAT` is the version of the pack's shape. Raise it when a key changes meaning or goes away. A new
key does not raise it, so a reader ignores keys it does not know.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from . import __version__, scenarios
from .conventions import SEMCONV_GENAI_COMMIT
from .model import Response, Scenario

FORMAT = 1
# The call plan: how the app calls the SDK. A driver in another language reads these keys.
CALL = (
    "id",
    "provider",
    "request_model",
    "stream",
    "stream_usage",
    "tools",
    "read_events",
    "with_block",
    "asynchronous",
    "api",
    "entrypoint",
    "retries",
)


def call_plan(sc: Scenario) -> dict[str, Any]:
    return {key: getattr(sc, key) for key in CALL}


def response(r: Response) -> dict[str, Any]:
    """One response as the mock server sends it. `events` keeps the order of the stream."""
    out: dict[str, Any] = {"status": r.status, "headers": dict(r.headers)}
    if r.body is not None:
        out["body"] = r.body
    else:
        out["events"] = [{"event": e.event, "data": e.data} for e in r.events or ()]
        out["drop_after"] = r.drop_after
    return out


def scenario(sc: Scenario) -> dict[str, Any]:
    return call_plan(sc) | {
        "title": sc.title,
        "catches": sc.catches,
        "tags": list(sc.tags),
        "depends_on": scenarios.depends_on(sc.id),
        "path": f"/s/{sc.key}",
        "responses": [response(r) for r in sc.responses],
        # The response that the truth describes: the last one, for a call that the SDK retries.
        "truth_response": sc.responses.index(sc.response),
        "truth": asdict(scenarios.truth(sc)),
    }


def export() -> dict[str, Any]:
    """The pack. It has no date, so the same spantruth writes the same bytes."""
    return {
        "format": FORMAT,
        "spantruth": __version__,
        "semconv_genai": SEMCONV_GENAI_COMMIT,
        "scenarios": {sid: scenario(sc) for sid, sc in scenarios.SCENARIOS.items()},
    }


def calls_only() -> dict[str, dict[str, Any]]:
    """The call plans alone, keyed by scenario ID, for a driver that needs nothing else."""
    return {sid: call_plan(sc) for sid, sc in scenarios.SCENARIOS.items()}


def dumps(data: dict[str, Any]) -> str:
    # Not sorted: a body and an event keep the key order of the wire.
    return json.dumps(data, indent=1) + "\n"
