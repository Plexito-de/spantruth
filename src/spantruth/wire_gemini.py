"""Gemini API wire format (`generateContent`, `streamGenerateContent?alt=sse`): builders and truth.

Shapes follow the REST reference, https://ai.google.dev/api/generate-content, in the camelCase JSON
that the API sends. tests/test_wire.py parses every body and every chunk with the `google-genai`
SDK's own types, so a fixture the SDK would reject fails the suite.

Two counts differ from the OpenTelemetry totals, and the truth below converts them:

- `promptTokenCount` already holds `cachedContentTokenCount`: "When `cached_content` is set, this
  is still the total effective prompt size" (https://ai.google.dev/api/generate-content#UsageMetadata,
  read 2026-11-26). So `gen_ai.usage.input_tokens` is `promptTokenCount`.
- `candidatesTokenCount` leaves out `thoughtsTokenCount`: `totalTokenCount` is their sum with the
  prompt. The conventions put reasoning tokens inside `gen_ai.usage.output_tokens`, so the output
  total is `candidatesTokenCount + thoughtsTokenCount`.

A stream carries a running `usageMetadata` on every chunk, as recorded from the live API
(https://github.com/faremeter/interchange/tree/HEAD/packages/inference-discovery-google-genai/sessions/google-genai,
gemini-2.5-flash and gemini-3.5-flash): `promptTokenCount` on every chunk, `candidatesTokenCount`
counted so far, `thoughtsTokenCount` from the first chunk once thinking is done, `totalTokenCount`
their sum, and the final counts on the last chunk. `modelVersion` equals the requested model id.
"""

from __future__ import annotations

from .model import Event, Response, Truth

# "Service tier of the request", in the REST UsageMetadata. The SDK's usage type does not
# declare it, and the SDK's parser drops it.
SERVICE_TIER = "standard"


def _usage(prompt: int, candidates: int | None, cached: int = 0, thoughts: int = 0) -> dict:
    """One `usageMetadata`. `candidates` None is a stream chunk with no answer text yet."""
    u: dict = {"promptTokenCount": prompt}
    if candidates is not None:
        u["candidatesTokenCount"] = candidates
    if cached:
        u["cachedContentTokenCount"] = cached
    u["totalTokenCount"] = prompt + (candidates or 0) + thoughts
    u["promptTokensDetails"] = [{"modality": "TEXT", "tokenCount": prompt}]
    if cached:
        u["cacheTokensDetails"] = [{"modality": "TEXT", "tokenCount": cached}]
    if thoughts:
        u["thoughtsTokenCount"] = thoughts
    u["serviceTier"] = SERVICE_TIER
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
            "responseId": id,
        }
    )


def running(total: int, chunks: int) -> list[int]:
    """`candidatesTokenCount` after each chunk: rising, and `total` on the last one."""
    return [total * (i + 1) // chunks for i in range(chunks)]


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
    """A stream: one chunk per text part, each with the usage so far, the finish on the last."""
    events = [
        Event(
            {
                "candidates": [_candidate(part, finish if i == len(parts) - 1 else None)],
                "usageMetadata": _usage(prompt, so_far, cached, thoughts),
                "modelVersion": model,
                "responseId": id,
            }
        )
        for i, (part, so_far) in enumerate(zip(parts, running(candidates, len(parts)), strict=True))
    ]
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
    """Derive the truth from what the app received. Only consumed chunks count in a stream.

    A stream the app leaves early ends on a running count. The prompt side is final on every chunk,
    so input and cache counts are graded. The output count so far is not the call's output, which
    only the last chunk carries, so output is not graded, and the final output count is an unread
    count: reporting it proves the library read past the point where the app stopped.
    """
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
    complete = len(seen) == len(response.events)
    known = _tokens(seen[-1]["usageMetadata"])
    if not complete:
        known |= {"output_tokens": None}
    finishes = tuple(
        c["finishReason"] for d in seen for c in d["candidates"] if "finishReason" in c
    )
    return Truth(
        request_model=request_model,
        response_model=seen[0]["modelVersion"],
        response_id=seen[0]["responseId"],
        finish_reasons=finishes or None,
        unread_counts=tuple(
            v for k, v in final.items() if v and known[k] != v and k == "output_tokens"
        ),
        **known,
    )
