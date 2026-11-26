"""A small, deliberately plain instrumentation of the OpenAI and Anthropic SDKs.

It exists to test the suite, not to be used. `control-honest` runs it as written and has to
pass every check. `control-faulty` runs it with known faults switched on and has to fail
exactly the checks those faults target. If either control moves, the suite is broken.

Content capture follows OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT, off by default, as
in the OpenTelemetry GenAI instrumentations: `true`, `span_only` or `span_and_event` records the
prompt and the completion text as `gen_ai.input.messages` and `gen_ai.output.messages`.

With a meter provider, each call that ends with usage also records the token histograms
`gen_ai.client.inference.operation.input_tokens` and `...output_tokens`, with the span's counts.

Faults:
    anthropic-raw-input         report Anthropic's wire `input_tokens`, without cache tokens
    close-leaks-span            `stream.close()` does not end the span
    content-first-chunk         a stream's recorded completion text is its first chunk only
    content-ignores-off         prompt and completion text are recorded with the switch off
    errors-as-success           exceptions are not recorded on the span
    estimate-tokens             a stream without usage gets an estimate from the text length
    metric-reasoning-left-out   the output token metric leaves out reasoning tokens
    retry-as-error              a retried operation that succeeds still sets Error status
"""

from __future__ import annotations

import functools
import json
import os
from typing import Any

from opentelemetry.trace import SpanKind, Status, StatusCode, Tracer

CONTENT_SWITCH = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"
CONTENT_ON = {"true", "span_only", "span_and_event"}


def _parts(text: str) -> list[dict[str, str]]:
    return [{"type": "text", "content": text}]


# The bucket boundaries the GenAI conventions advise for the token histograms.
TOKEN_BUCKETS = [4**i for i in range(14)]


class _Meters:
    def __init__(self, meter_provider: Any) -> None:
        meter = meter_provider.get_meter("spantruth.reference")
        self.input, self.output = (
            meter.create_histogram(
                f"gen_ai.client.inference.operation.{direction}_tokens",
                unit="{token}",
                explicit_bucket_boundaries_advisory=TOKEN_BUCKETS,
            )
            for direction in ("input", "output")
        )


class _Call:
    """The span of one call and the facts gathered for it."""

    def __init__(
        self,
        tracer: Tracer,
        provider: str,
        model: str,
        faults: set[str],
        prompt: Any = None,
        meters: _Meters | None = None,
    ) -> None:
        self.span = tracer.start_span(
            f"chat {model}",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": provider,
                "gen_ai.request.model": model,
            },
        )
        self.provider, self.faults, self.meters = provider, faults, meters
        self.model = model
        # The usage and model values set on the span, which the token metrics repeat.
        self.tokens: dict[str, Any] = {}
        self.failed = False
        self.finish: list[str] = []
        self.text = ""
        self.chunks: list[str] = []
        self.capture = os.environ.get(CONTENT_SWITCH, "").lower() in CONTENT_ON or (
            "content-ignores-off" in faults
        )
        if self.capture and isinstance(prompt, list):
            messages = [
                {"role": m["role"], "parts": _parts(m["content"])}
                for m in prompt
                if isinstance(m.get("content"), str)
            ]
            self.set("gen_ai.input.messages", json.dumps(messages))
        self.usage_seen = False
        self.ended = False
        self.response_id = ""

    def set(self, key: str, value: Any) -> None:
        if value is not None:
            if key == "gen_ai.response.id":
                self.response_id = str(value)
            if key.startswith("gen_ai.usage.") or key == "gen_ai.response.model":
                self.tokens[key] = value
            self.span.set_attribute(key, value)

    def usage(
        self, inp: int, out: int | None, read: int = 0, write: int = 0, reasoning: int = 0
    ) -> None:
        self.usage_seen = True
        if self.provider == "anthropic" and "anthropic-raw-input" not in self.faults:
            inp += read + write
        self.set("gen_ai.usage.input_tokens", inp)
        self.set("gen_ai.usage.output_tokens", out)
        self.set("gen_ai.usage.cache_read.input_tokens", read or None)
        self.set("gen_ai.usage.cache_write.input_tokens", write or None)
        self.set("gen_ai.usage.reasoning.output_tokens", reasoning or None)

    def add_text(self, chunk: str) -> None:
        self.text += chunk
        self.chunks.append(chunk)

    def _record_output(self) -> None:
        text = self.text
        if "content-first-chunk" in self.faults and self.chunks:
            text = self.chunks[0]
        message = {"role": "assistant", "parts": _parts(text)}
        if self.finish:
            message["finish_reason"] = self.finish[0]
        self.set("gen_ai.output.messages", json.dumps([message]))

    def fail(self, exc: BaseException) -> None:
        self.failed = True
        if "errors-as-success" not in self.faults:
            self.span.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
            self.set("error.type", type(exc).__name__)
        self.end()

    def end(self) -> None:
        if self.ended:
            return
        self.ended = True
        if self.finish:
            self.set("gen_ai.response.finish_reasons", self.finish)
        if self.capture and self.text:
            self._record_output()
        if not self.usage_seen and self.text and "estimate-tokens" in self.faults:
            self.set("gen_ai.usage.output_tokens", max(1, len(self.text) // 4))
        if "retry-as-error" in self.faults and (
            self.response_id.endswith("-retry") or self.response_id.endswith("_retry")
        ):
            self.span.set_status(Status(StatusCode.ERROR, "429 Too Many Requests"))
        if not self.failed:
            self._record_metrics()
        self.span.end()

    def _record_metrics(self) -> None:
        """The token histograms, with the counts the span carries."""
        if self.meters is None:
            return
        attributes = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": self.provider,
            "gen_ai.request.model": self.model,
        }
        if "gen_ai.response.model" in self.tokens:
            attributes["gen_ai.response.model"] = self.tokens["gen_ai.response.model"]
        inp = self.tokens.get("gen_ai.usage.input_tokens")
        out = self.tokens.get("gen_ai.usage.output_tokens")
        if out is not None and "metric-reasoning-left-out" in self.faults:
            out -= self.tokens.get("gen_ai.usage.reasoning.output_tokens") or 0
        if inp is not None:
            self.meters.input.record(inp, attributes)
        if out is not None:
            self.meters.output.record(out, attributes)


def _openai_response(call: _Call, r: Any) -> None:
    call.set("gen_ai.response.model", r.model)
    call.set("gen_ai.response.id", r.id)
    call.finish = [c.finish_reason for c in r.choices]
    if r.choices and r.choices[0].message.content:
        call.add_text(r.choices[0].message.content)
    _openai_usage(call, r.usage)


def _openai_usage(call: _Call, u: Any) -> None:
    cached = getattr(u.prompt_tokens_details, "cached_tokens", 0) or 0
    written = getattr(u.prompt_tokens_details, "cache_write_tokens", 0) or 0
    reasoning = getattr(u.completion_tokens_details, "reasoning_tokens", 0) or 0
    call.usage(
        u.prompt_tokens, u.completion_tokens, read=cached, write=written, reasoning=reasoning
    )


def _openai_chunk(call: _Call, c: Any) -> None:
    call.set("gen_ai.response.model", c.model)
    call.set("gen_ai.response.id", c.id)
    for choice in c.choices:
        if choice.delta.content:
            call.add_text(choice.delta.content)
        if choice.finish_reason:
            call.finish.append(choice.finish_reason)
    if c.usage:
        _openai_usage(call, c.usage)


def _responses_event(call: _Call, e: Any) -> None:
    if e.type in ("response.created", "response.completed"):
        call.set("gen_ai.response.model", e.response.model)
        call.set("gen_ai.response.id", e.response.id)
    if e.type == "response.completed" and e.response.usage:
        u = e.response.usage
        call.usage(
            u.input_tokens,
            u.output_tokens,
            read=u.input_tokens_details.cached_tokens or 0,
            reasoning=u.output_tokens_details.reasoning_tokens or 0,
        )
    elif e.type == "response.output_text.delta":
        call.add_text(e.delta)


def _anthropic_response(call: _Call, r: Any) -> None:
    call.set("gen_ai.response.model", r.model)
    call.set("gen_ai.response.id", r.id)
    call.finish = [r.stop_reason]
    for block in r.content:
        if block.type == "text":
            call.add_text(block.text)
    u = r.usage
    thinking = getattr(getattr(u, "output_tokens_details", None), "thinking_tokens", 0) or 0
    call.usage(
        u.input_tokens,
        u.output_tokens,
        u.cache_read_input_tokens or 0,
        u.cache_creation_input_tokens or 0,
        reasoning=thinking,
    )


def _anthropic_event(call: _Call, e: Any) -> None:
    if e.type == "message_start":
        m = e.message
        call.set("gen_ai.response.model", m.model)
        call.set("gen_ai.response.id", m.id)
        u = m.usage
        call.usage(
            u.input_tokens, None, u.cache_read_input_tokens or 0, u.cache_creation_input_tokens or 0
        )
    elif e.type == "message_delta":
        # Output is cumulative here. Input and cache counts, when repeated, are not added again.
        call.set("gen_ai.usage.output_tokens", e.usage.output_tokens)
        thinking = (
            getattr(getattr(e.usage, "output_tokens_details", None), "thinking_tokens", 0) or 0
        )
        if thinking:
            call.set("gen_ai.usage.reasoning.output_tokens", thinking)
        if e.delta.stop_reason:
            call.finish = [e.delta.stop_reason]
    elif e.type == "content_block_delta" and e.delta.type == "text_delta":
        call.add_text(e.delta.text)


class _Stream:
    def __init__(self, inner: Any, call: _Call, on_event) -> None:
        self._inner, self._call, self._on_event = inner, call, on_event

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __iter__(self):
        try:
            for event in self._inner:
                self._on_event(self._call, event)
                yield event
        except Exception as exc:
            self._call.fail(exc)
            raise
        self._call.end()

    def __aiter__(self):
        return self._agen()

    async def _agen(self):
        try:
            async for event in self._inner:
                self._on_event(self._call, event)
                yield event
        except Exception as exc:
            self._call.fail(exc)
            raise
        self._call.end()

    def parse(self) -> Any:
        parsed = self._inner.parse()
        if hasattr(parsed, "__await__"):

            async def _aparse():
                res = await parsed
                if hasattr(res, "__aiter__"):
                    return _Stream(res, self._call, self._on_event)
                return res

            return _aparse()
        if hasattr(parsed, "__iter__"):
            return _Stream(parsed, self._call, self._on_event)
        return parsed

    def _closed(self) -> None:
        if "close-leaks-span" not in self._call.faults:
            self._call.end()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._inner.__exit__(*exc)
        self._closed()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self._inner.__aexit__(*exc)
        self._closed()

    def close(self):
        result = self._inner.close()
        if hasattr(result, "__await__"):
            return self._aclose(result)
        self._closed()
        return result

    async def _aclose(self, pending):
        await pending
        self._closed()


def _wrap(
    tracer: Tracer,
    provider: str,
    faults: set[str],
    on_result,
    on_event,
    create,
    is_async,
    meters: _Meters | None = None,
):
    def begin(kwargs: dict) -> _Call:
        prompt = kwargs.get("messages", kwargs.get("input"))
        return _Call(tracer, provider, kwargs.get("model", ""), faults, prompt, meters)

    # Decided by class, not by inspection: the SDKs wrap async methods in sync decorators.
    if is_async:

        @functools.wraps(create)
        async def acall(self, *args, **kwargs):
            call = begin(kwargs)
            try:
                result = await create(self, *args, **kwargs)
            except Exception as exc:
                call.fail(exc)
                raise
            return _finish(call, result, kwargs, on_result, on_event)

        return acall

    @functools.wraps(create)
    def scall(self, *args, **kwargs):
        call = begin(kwargs)
        try:
            result = create(self, *args, **kwargs)
        except Exception as exc:
            call.fail(exc)
            raise
        return _finish(call, result, kwargs, on_result, on_event)

    return scall


def _finish(call: _Call, result: Any, kwargs: dict, on_result, on_event) -> Any:
    if kwargs.get("stream"):
        return _Stream(result, call, on_event)
    on_result(call, result)
    call.end()
    return result


def instrument(tracer_provider, faults: set[str] = frozenset(), meter_provider=None) -> None:
    tracer = tracer_provider.get_tracer("spantruth.reference")
    meters = _Meters(meter_provider) if meter_provider is not None else None
    import anthropic.resources.messages as am
    import openai.resources.chat.completions as oc
    import openai.resources.responses as orr

    for cls, is_async in ((oc.Completions, False), (oc.AsyncCompletions, True)):
        cls.create = _wrap(
            tracer,
            "openai",
            set(faults),
            _openai_response,
            _openai_chunk,
            cls.create,
            is_async,
            meters,
        )
    for cls, is_async in ((orr.Responses, False), (orr.AsyncResponses, True)):
        # Only streams are exercised so far, so a plain response is not read.
        cls.create = _wrap(
            tracer,
            "openai",
            set(faults),
            lambda call, r: None,
            _responses_event,
            cls.create,
            is_async,
            meters,
        )
    for cls, is_async in ((am.Messages, False), (am.AsyncMessages, True)):
        cls.create = _wrap(
            tracer,
            "anthropic",
            set(faults),
            _anthropic_response,
            _anthropic_event,
            cls.create,
            is_async,
            meters,
        )
