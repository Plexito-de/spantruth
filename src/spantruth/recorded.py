"""Replay a recorded provider response, in kartsan03/wiretruth's fixture format.

The scenarios in `scenarios.py` build their responses in code. A recorded fixture is the other
source: a real provider stream, kept byte for byte, with the provenance of where it came from.
Only what replay needs is read: one exchange, its status, its JSON body or SSE frames, and how it
ended. The tier says where the bytes came from: `recorded` from the real API, `documented` from the
provider's documentation, `derived` from another fixture by a named transform.
Format: https://github.com/kartsan03/wiretruth/blob/main/docs/formats.md
"""

from __future__ import annotations

import json
from pathlib import Path

from .model import Event, Response

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FORMAT = "wiretruth.fixture/v1"


def frame(raw: str) -> Event:
    """One recorded SSE frame, `event: <name>\\ndata: <json>`, as an Event."""
    name, data = None, []
    for line in raw.splitlines():
        if line.startswith("event:"):
            name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data.append(line[len("data:") :].lstrip())
    return Event(data="\n".join(data), event=name)


def load(path: Path) -> Response:
    """The response of a recorded fixture. An `abort` end drops the connection after the end.

    The format names a normal end `clean`. `complete` is read the same way.
    """
    doc = json.loads(Path(path).read_text())
    if doc.get("format") != FORMAT:
        raise ValueError(f"{path}: format {doc.get('format')!r}, expected {FORMAT!r}")
    if len(doc["exchanges"]) != 1:
        raise ValueError(f"{path}: replay needs exactly one exchange")
    response = doc["exchanges"][0]["response"]
    end = response.get("end", "clean")
    if end not in ("clean", "complete", "abort"):
        raise ValueError(f"{path}: unknown end {end!r}")
    body = response["body"]
    if "json" in body:
        if end == "abort":
            raise ValueError(f"{path}: an abort end needs an sse body")
        return Response(status=response["status"], body=body["json"])
    events = tuple(frame(raw) for raw in body["sse"])
    return Response(
        status=response["status"],
        events=events,
        drop_after=len(events) if end == "abort" else None,
    )


def requested_model(path: Path) -> str:
    return json.loads(Path(path).read_text())["provenance"]["requested_model"]


def tier(path: Path) -> str:
    """Where the bytes came from: `recorded`, `documented` or `derived`."""
    return json.loads(Path(path).read_text())["tier"]
