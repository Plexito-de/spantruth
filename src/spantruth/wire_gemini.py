"""Gemini API wire format (`generateContent`, `streamGenerateContent?alt=sse`): builders and truth.

Shapes follow the REST reference, https://ai.google.dev/api/generate-content, in the camelCase JSON
that the API sends. tests/test_wire.py parses every body and every chunk with the `google-genai`
SDK's own types, so a fixture the SDK would reject fails the suite.

Two counts differ from the OpenTelemetry totals, and the truth below converts them:

- `promptTokenCount` already holds `cachedContentTokenCount`, as OpenAI's `prompt_tokens` holds
  its cached tokens. So `gen_ai.usage.input_tokens` is `promptTokenCount`.
- `candidatesTokenCount` leaves out `thoughtsTokenCount`: `totalTokenCount` is their sum with the
  prompt. The conventions put reasoning tokens inside `gen_ai.usage.output_tokens`, so the output
  total is `candidatesTokenCount + thoughtsTokenCount`.
"""

from __future__ import annotations

from .model import Event, Response, Truth

CREATED = "2026-11-26T09:00:00.000000Z"


def _usage(prompt: int, candidates: int | None, cached: int = 0, thoughts: int = 0) -> dict:
    """The `usageMetadata` of a response. `candidates` None is a stream chunk before the last.

    A chunk before the last carries the prompt side only, and the last chunk carries every count.
    """
    u: dict = {"promptTokenCount": prompt}
    total = prompt
    if candidates is not None:
        u["candidatesTokenCount"] = candidates
        total += candidates
        if thoughts:
            u["thoughtsTokenCount"] = thoughts
            total += thoughts
    if cached:
        u["cachedContentTokenCount"] = cached
    u["totalTokenCount"] = total
    u["promptTokensDetails"] = [{"modality": "TEXT", "tokenCount": prompt}]
    if cached:
        u["cacheTokensDetails"] = [{"modality": "TEXT", "tokenCount": cached}]
    if candidates is not None:
        u["candidatesTokensDetails"] = [{"modality": "TEXT", "tokenCount": candidates}]
    return u


def _candidate(text: str, finish: str | None) -> dict:
    c: dict = {"content": {"parts": [{"text": text}], "role": "model"}, "index": 0}
    if finish:
        c["finishReason"] = finish
    return c


def response(
    *,
    id: str,
    model: str,
    text: str,
    finish: str,
    prompt: int,
    candidates: int,
    cached: int = 0,
    thoughts: int = 0,
) -> Response:
    return Response(
        body={
            "candidates": [_candidate(text, finish)],
            "usageMetadata": _usage(prompt, candidates, cached, thoughts),
            "modelVersion": model,
            "createTime": CREATED,
            "responseId": id,
        }
    )


def chunks(
    *,
    id: str,
    model: str,
    parts: tuple[str, ...],
    finish: str,
    prompt: int,
    candidates: int,
    cached: int = 0,
    thoughts: int = 0,
    drop_after: int | None = None,
) -> Response:
    """A stream: one chunk per text part, with the finish reason and full usage on the last one."""
    events = []
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        events.append(
            Event(
                {
                    "candidates": [_candidate(part, finish if last else None)],
                    "usageMetadata": _usage(prompt, candidates if last else None, cached, thoughts),
                    "modelVersion": model,
                    "createTime": CREATED,
                    "responseId": id,
                }
            )
        )
    return Response(events=tuple(events), drop_after=drop_after)


def error(status: int, code: str, message: str) -> Response:
    """An error as the API sends it: `{"error": {"code", "message", "status"}}`."""
    return Response(
        status=status, body={"error": {"code": status, "message": message, "status": code}}
    )


def _tokens(usage: dict) -> dict:
    """Totals as the conventions define them, from one `usageMetadata`."""
    thoughts = usage.get("thoughtsTokenCount") or 0
    candidates = usage.get("candidatesTokenCount")
    return {
        "input_tokens": usage["promptTokenCount"],
        "output_tokens": None if candidates is None else candidates + thoughts,
        "cache_read_tokens": usage.get("cachedContentTokenCount") or None,
        "reasoning_tokens": thoughts or None,
    }


def truth(request_model: str, response: Response, read_events: int | None = None) -> Truth:
    """Derive the truth from what the app received. Only consumed chunks count in a stream."""
    if response.status >= 400:
        return Truth(request_model=request_model, error=f"HTTP {response.status}")
    if response.body is not None:
        body = response.body
        return Truth(
            request_model=request_model,
            response_model=body["modelVersion"],
            response_id=body["responseId"],
            finish_reasons=tuple(c["finishReason"] for c in body["candidates"]),
            **_tokens(body["usageMetadata"]),
        )
    if response.reaches_drop(read_events):
        return Truth(request_model=request_model, error="connection dropped")
    seen = [e.data for e in response.consumed(read_events)]
    final = _tokens(response.events[-1].data["usageMetadata"])
    known = _tokens(seen[-1]["usageMetadata"])
    finishes = tuple(
        c["finishReason"] for d in seen for c in d["candidates"] if "finishReason" in c
    )
    return Truth(
        request_model=request_model,
        response_model=seen[0]["modelVersion"],
        response_id=seen[0]["responseId"],
        finish_reasons=finishes or None,
        unread_counts=tuple(v for k, v in final.items() if v and known[k] is None),
        **known,
    )
