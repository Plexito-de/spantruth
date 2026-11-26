"""OpenAI Chat Completions wire format: response builders and the truth they imply.

Shapes follow the published API reference. tests/test_wire.py parses every body and every
chunk with the `openai` SDK's own types, so a fixture the SDK would reject fails the suite.
"""

from __future__ import annotations

from .model import Event, Response, Truth

CREATED = 1760000000
FINGERPRINT = "fp_wt0001"


# How a usage carries its two details objects. OpenAI sends both. OpenAI-compatible servers, such
# as vLLM and Ollama, can leave them out or send null: the API reference marks both as optional.
DETAILS = ("full", "absent", "null")


def _usage(
    prompt: int,
    completion: int,
    cached: int,
    reasoning: int,
    cache_write: int | None = None,
    details: str = "full",
) -> dict:
    """The `usage` of a completion. `prompt` includes the cached and the written tokens.

    `cache_write` is sent only when given: GPT-5.6 and later report the tokens a call wrote to
    the prompt cache, and bill them at their own rate. Earlier models send no such field.
    """
    usage: dict = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    if details not in DETAILS:
        raise ValueError(f"details {details!r}, expected one of {DETAILS}")
    if details != "full":
        if cached or reasoning or cache_write is not None:
            raise ValueError("a usage without details has no cache or reasoning counts")
        nulls = {"prompt_tokens_details": None, "completion_tokens_details": None}
        return usage | (nulls if details == "null" else {})
    prompt_details: dict = {"cached_tokens": cached, "audio_tokens": 0}
    if cache_write is not None:
        prompt_details["cache_write_tokens"] = cache_write
    return usage | {
        "prompt_tokens_details": prompt_details,
        "completion_tokens_details": {
            "reasoning_tokens": reasoning,
            "audio_tokens": 0,
            "accepted_prediction_tokens": 0,
            "rejected_prediction_tokens": 0,
        },
    }


def _tool_call(index: int, call_id: str, name: str, arguments: str) -> dict:
    return {
        "index": index,
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def completion(
    *,
    id: str,
    model: str,
    text: str | None,
    finish: str,
    prompt: int,
    completion_tokens: int,
    cached: int = 0,
    cache_write: int | None = None,
    reasoning: int = 0,
    tool_calls: tuple[tuple[str, str, str], ...] = (),
    details: str = "full",
) -> Response:
    message: dict = {"role": "assistant", "content": text, "refusal": None, "annotations": []}
    if tool_calls:
        message["tool_calls"] = [
            {k: v for k, v in _tool_call(i, *call).items() if k != "index"}
            for i, call in enumerate(tool_calls)
        ]
    return Response(
        body={
            "id": id,
            "object": "chat.completion",
            "created": CREATED,
            "model": model,
            "system_fingerprint": FINGERPRINT,
            "service_tier": "default",
            "choices": [
                {"index": 0, "message": message, "finish_reason": finish, "logprobs": None}
            ],
            "usage": _usage(prompt, completion_tokens, cached, reasoning, cache_write, details),
        }
    )


def chunks(
    *,
    id: str,
    model: str,
    parts: tuple[str, ...],
    finish: str,
    prompt: int,
    completion_tokens: int,
    cached: int = 0,
    cache_write: int | None = None,
    reasoning: int = 0,
    drop_after: int | None = None,
    include_usage: bool = True,
    tool_calls: tuple[tuple[str, str, str], ...] = (),
    details: str = "full",
    usage_on_finish: bool = False,
) -> Response:
    """A stream. With `include_usage`, as sent for `stream_options={"include_usage": True}`.

    OpenAI sends the usage in one more chunk, whose `choices` is empty. `usage_on_finish` puts it
    on the chunk that carries the finish reason instead, as DeepSeek and OpenRouter do.
    """

    def chunk(delta: dict, finish_reason: str | None = None) -> Event:
        return Event(
            {
                "id": id,
                "object": "chat.completion.chunk",
                "created": CREATED,
                "model": model,
                "system_fingerprint": FINGERPRINT,
                "service_tier": "default",
                "choices": [
                    {"index": 0, "delta": delta, "finish_reason": finish_reason, "logprobs": None}
                ],
                "usage": None,
            }
        )

    events = [chunk({"role": "assistant", "content": None if tool_calls else "", "refusal": None})]
    if tool_calls:
        for i, (call_id, name, args) in enumerate(tool_calls):
            events.append(
                chunk(
                    {
                        "tool_calls": [
                            {
                                "index": i,
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": ""},
                            }
                        ]
                    }
                )
            )
            if args:
                events.append(
                    chunk(
                        {
                            "tool_calls": [
                                {
                                    "index": i,
                                    "function": {"arguments": args},
                                }
                            ]
                        }
                    )
                )
    events += [chunk({"content": part}) for part in parts]
    events.append(chunk({}, finish))
    if include_usage:
        usage = _usage(prompt, completion_tokens, cached, reasoning, cache_write, details)
        if usage_on_finish:
            events[-1] = Event(events[-1].data | {"usage": usage})
        else:
            events.append(Event(chunk({}).data | {"choices": [], "usage": usage}))
    events.append(Event("[DONE]"))
    return Response(events=tuple(events), drop_after=drop_after)


def error(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> Response:
    return Response(
        status=status,
        headers=headers or {},
        body={"error": {"message": message, "type": "requests", "param": None, "code": code}},
    )


def truth(request_model: str, response: Response, read_events: int | None = None) -> Truth:
    """Derive the truth from what the app received.

    For a stream, only the events the app consumed count. An instrumentation sees the same
    events, so a value that never reached the app cannot be expected in the telemetry.
    """
    if response.status >= 400:
        return Truth(request_model=request_model, error=f"HTTP {response.status}")
    if response.body is not None:
        body = response.body
        return Truth(
            request_model=request_model,
            response_model=body["model"],
            response_id=body["id"],
            finish_reasons=tuple(c["finish_reason"] for c in body["choices"]),
            uncounted=_uncounted(body["usage"]),
            **_tokens(body["usage"]),
        )
    if response.reaches_drop(read_events):
        return Truth(request_model=request_model, error="connection dropped")
    seen = [e.data for e in response.consumed(read_events) if isinstance(e.data, dict)]
    every = [e.data for e in response.events or () if isinstance(e.data, dict)]
    usage = next((d["usage"] for d in seen if d.get("usage")), None)
    full = next((d["usage"] for d in every if d.get("usage")), None)
    finishes = tuple(
        c["finish_reason"] for d in seen for c in d["choices"] if c.get("finish_reason")
    )
    return Truth(
        request_model=request_model,
        response_model=seen[0]["model"] if seen else None,
        response_id=seen[0]["id"] if seen else None,
        finish_reasons=finishes or None,
        unread_counts=tuple(v for v in _tokens(full).values() if v) if full and not usage else (),
        no_usage=full is None,
        uncounted=_uncounted(usage) if usage else (),
        **(_tokens(usage) if usage else {}),
    )


def responses_truth(
    request_model: str, response: Response, read_events: int | None = None
) -> Truth:
    """The truth of a Responses API stream. Only the failure paths are graded so far."""
    if response.status >= 400:
        return Truth(request_model=request_model, error=f"HTTP {response.status}")
    if response.reaches_drop(read_events):
        return Truth(request_model=request_model, error="connection dropped")
    raise NotImplementedError("a Responses stream that ends is not graded yet")


def _tokens(usage: dict) -> dict:
    """`prompt_tokens` already holds the cached and the written tokens, unlike Anthropic's count.

    Each details object, and each count inside one, is optional. An absent count is not graded.
    """
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    return {
        "input_tokens": usage["prompt_tokens"],
        "output_tokens": usage["completion_tokens"],
        "cache_read_tokens": prompt_details.get("cached_tokens") or None,
        "cache_write_tokens": prompt_details.get("cache_write_tokens") or None,
        "reasoning_tokens": completion_details.get("reasoning_tokens") or None,
    }


def _uncounted(usage: dict) -> tuple[str, ...]:
    """The counts the usage has no field for, as opposed to a field that holds zero.

    A server that sends no `prompt_tokens_details` reports no cache count at all, so a non-zero
    cache count on a span did not come from the wire. Only the counts that a details object holds
    are listed: `prompt_tokens` and `completion_tokens` are required.
    """
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    fields = {
        "cache_read_tokens": prompt_details.get("cached_tokens"),
        "reasoning_tokens": completion_details.get("reasoning_tokens"),
    }
    return tuple(fact for fact, value in fields.items() if value is None)
