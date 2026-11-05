"""Anthropic Messages wire format: response builders and the truth they imply.

Shapes follow the published API reference. tests/test_wire.py parses every body and every
event with the `anthropic` SDK's own types, so a fixture the SDK would reject fails the suite.
"""

from __future__ import annotations

from .model import Event, Response, Truth


def _usage(
    input_tokens: int,
    output_tokens: int,
    cache_read: int,
    cache_write: int,
    thinking: int = 0,
) -> dict:
    u: dict = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
    }
    if thinking:
        u["output_tokens_details"] = {"thinking_tokens": thinking}
    return u


def message(
    *,
    id: str,
    model: str,
    text: str | None,
    stop: str,
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
    cache_write: int = 0,
    thinking: str | None = None,
    signature: str | None = None,
    thinking_tokens: int = 0,
    tool_uses: tuple[tuple[str, str, dict], ...] = (),
) -> Response:
    content: list[dict] = []
    if thinking is not None:
        content.append(
            {
                "type": "thinking",
                "thinking": thinking,
                "signature": signature or "dGVzdC1zaWduYXR1cmU=",
            }
        )
    if text:
        content.append({"type": "text", "text": text})
    content += [{"type": "tool_use", "id": i, "name": n, "input": a} for i, n, a in tool_uses]
    return Response(
        body={
            "id": id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content,
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": _usage(
                input_tokens, output_tokens, cache_read, cache_write, thinking=thinking_tokens
            ),
        }
    )


def events(
    *,
    id: str,
    model: str,
    parts: tuple[str, ...],
    stop: str,
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
    cache_write: int = 0,
    thinking_parts: tuple[str, ...] = (),
    signature: str | None = None,
    thinking_tokens: int = 0,
    drop_after: int | None = None,
    cumulative_delta: bool = False,
    error_after: int | None = None,
) -> Response:
    """A stream as the API sends it.

    `message_delta` carries only the final output count, unless `cumulative_delta` is set: the
    API may also repeat the cumulative input and cache counts there, and a library that adds
    them to `message_start` counts them twice. `error_after` replaces the rest of the stream with
    an `error` event after that many events, as the API does when it is overloaded mid-stream.
    """

    def ev(kind: str, **data: object) -> Event:
        return Event({"type": kind, **data}, event=kind)

    start = {
        "id": id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": _usage(input_tokens, 1, cache_read, cache_write),
    }
    stream = [ev("message_start", message=start)]
    idx = 0
    if thinking_parts:
        stream.append(
            ev(
                "content_block_start",
                index=idx,
                content_block={"type": "thinking", "thinking": "", "signature": ""},
            )
        )
        stream += [
            ev("content_block_delta", index=idx, delta={"type": "thinking_delta", "thinking": p})
            for p in thinking_parts
        ]
        stream.append(
            ev(
                "content_block_delta",
                index=idx,
                delta={
                    "type": "signature_delta",
                    "signature": signature or "dGVzdC1zaWduYXR1cmU=",
                },
            )
        )
        stream.append(ev("content_block_stop", index=idx))
        idx += 1

    stream.append(ev("content_block_start", index=idx, content_block={"type": "text", "text": ""}))
    stream += [
        ev("content_block_delta", index=idx, delta={"type": "text_delta", "text": p}) for p in parts
    ]
    stream.append(ev("content_block_stop", index=idx))

    delta_usage = (
        _usage(input_tokens, output_tokens, cache_read, cache_write, thinking=thinking_tokens)
        if cumulative_delta
        else (
            {
                "output_tokens": output_tokens,
                "output_tokens_details": {"thinking_tokens": thinking_tokens},
            }
            if thinking_tokens
            else {"output_tokens": output_tokens}
        )
    )
    stream += [
        ev(
            "message_delta",
            delta={"stop_reason": stop, "stop_sequence": None},
            usage=delta_usage,
        ),
        ev("message_stop"),
    ]
    if error_after is not None:
        overloaded = {"type": "overloaded_error", "message": "Overloaded"}
        stream = [*stream[:error_after], ev("error", error=overloaded)]
    return Response(events=tuple(stream), drop_after=drop_after)


def error(status: int, kind: str, message: str, headers: dict[str, str] | None = None) -> Response:
    return Response(
        status=status,
        headers=headers or {},
        body={"type": "error", "error": {"type": kind, "message": message}},
    )


def truth(request_model: str, response: Response, read_events: int | None = None) -> Truth:
    """Derive the truth from what the app received.

    Anthropic's `input_tokens` excludes cached tokens. The GenAI conventions require the
    instrumentation to add cache reads and cache writes, so the truth carries the sum.
    `message_start` carries the input counts, so they are known as soon as the app has it.
    """
    if response.status >= 400:
        return Truth(request_model=request_model, error=f"HTTP {response.status}")
    if response.body is not None:
        body = response.body
        return Truth(
            request_model=request_model,
            response_model=body["model"],
            response_id=body["id"],
            finish_reasons=(body["stop_reason"],),
            **_tokens(body["usage"], body["usage"]["output_tokens"]),
        )
    if response.reaches_drop(read_events):
        return Truth(request_model=request_model, error="connection dropped")
    seen = [e.data for e in response.consumed(read_events) if isinstance(e.data, dict)]
    failure = next((d["error"]["type"] for d in seen if d["type"] == "error"), None)
    if failure:
        return Truth(request_model=request_model, error=f"error event: {failure}")
    every = [e.data for e in response.events or () if isinstance(e.data, dict)]
    start = next((d["message"] for d in seen if d["type"] == "message_start"), None)
    delta = next((d for d in reversed(seen) if d["type"] == "message_delta"), None)
    final = next((d for d in reversed(every) if d["type"] == "message_delta"), None)
    output = delta["usage"]["output_tokens"] if delta else None
    usage = dict(start["usage"]) if start else {}
    if delta and "output_tokens_details" in delta.get("usage", {}):
        usage["output_tokens_details"] = delta["usage"]["output_tokens_details"]
    unread: list[int] = []
    if final and not delta:
        unread.append(final["usage"]["output_tokens"])
        if thinking := final["usage"].get("output_tokens_details", {}).get("thinking_tokens"):
            unread.append(thinking)
    return Truth(
        request_model=request_model,
        response_model=start["model"] if start else None,
        response_id=start["id"] if start else None,
        finish_reasons=(delta["delta"]["stop_reason"],) if delta else None,
        unread_counts=tuple(unread),
        **(_tokens(usage, output) if start else {}),
    )


def _tokens(usage: dict, output_tokens: int | None) -> dict:
    read = usage.get("cache_read_input_tokens") or 0
    write = usage.get("cache_creation_input_tokens") or 0
    details = usage.get("output_tokens_details") or {}
    thinking = (
        details.get("thinking_tokens")
        if isinstance(details, dict)
        else getattr(details, "thinking_tokens", None)
    )
    return {
        "input_tokens": usage["input_tokens"] + read + write,
        "output_tokens": output_tokens,
        "cache_read_tokens": read or None,
        "cache_write_tokens": write or None,
        "reasoning_tokens": thinking or None,
    }
