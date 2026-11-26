"""A small, deliberately plain instrumentation of the OpenAI, Anthropic and google-genai SDKs.

It exists to test the suite, not to be used. `control-honest` runs it as written and has to
pass every check. `control-faulty` runs it with known faults switched on and has to fail
exactly the checks those faults target. If either control moves, the suite is broken.

Faults:
    anthropic-raw-input   report Anthropic's wire `input_tokens`, without cache tokens
    close-leaks-span      `stream.close()` does not end the span
    errors-as-success     exceptions are not recorded on the span
    estimate-tokens       a stream without usage gets an estimate from the text length
    gemini-candidates-output  report Gemini's `candidatesTokenCount` as output, without thoughts
    retry-as-error        a retried operation that succeeds still sets Error status
"""

from __future__ import annotations

import functools
from typing import Any

from opentelemetry.trace import SpanKind, Status, StatusCode, Tracer


class _Call:
    """The span of one call and the facts gathered for it."""

    def __init__(
        self, tracer: Tracer, provider: str, model: str, faults: set[str], operation: str = "chat"
    ) -> None:
        self.span = tracer.start_span(
            f"{operation} {model}",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.operation.name": operation,
                "gen_ai.provider.name": provider,
                "gen_ai.request.model": model,
            },
        )
        self.provider, self.faults = provider, faults
        self.finish: list[str] = []
        self.text = ""
        self.usage_seen = False
        self.ended = False
        self.response_id = ""

    def set(self, key: str, value: Any) -> None:
        if value is not None:
            if key == "gen_ai.response.id":
                self.response_id = str(value)
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

    def fail(self, exc: BaseException) -> None:
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
        if not self.usage_seen and self.text and "estimate-tokens" in self.faults:
            self.set("gen_ai.usage.output_tokens", max(1, len(self.text) // 4))
        if "retry-as-error" in self.faults and (
            self.response_id.endswith("-retry") or self.response_id.endswith("_retry")
        ):
            self.span.set_status(Status(StatusCode.ERROR, "429 Too Many Requests"))
        self.span.end()


def _openai_response(call: _Call, r: Any) -> None:
    call.set("gen_ai.response.model", r.model)
    call.set("gen_ai.response.id", r.id)
    call.finish = [c.finish_reason for c in r.choices]
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
            call.text += choice.delta.content
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
        call.text += e.delta


def _anthropic_response(call: _Call, r: Any) -> None:
    call.set("gen_ai.response.model", r.model)
    call.set("gen_ai.response.id", r.id)
    call.finish = [r.stop_reason]
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
        call.text += e.delta.text


def _gemini_usage(call: _Call, u: Any) -> None:
    """`promptTokenCount` holds the cached tokens, `candidatesTokenCount` leaves out thoughts."""
    if u is None or u.prompt_token_count is None:
        return
    thoughts = u.thoughts_token_count or 0
    out = u.candidates_token_count
    if out is None:
        # A chunk before the last: the prompt side only. Output comes with the last chunk.
        call.usage_seen = True
        call.set("gen_ai.usage.input_tokens", u.prompt_token_count)
        call.set("gen_ai.usage.cache_read.input_tokens", u.cached_content_token_count or None)
        return
    if "gemini-candidates-output" not in call.faults:
        out += thoughts
    cached = u.cached_content_token_count or 0
    call.usage(u.prompt_token_count, out, read=cached, reasoning=thoughts)


def _gemini_response(call: _Call, r: Any) -> None:
    call.set("gen_ai.response.model", r.model_version)
    call.set("gen_ai.response.id", r.response_id)
    finish = [c.finish_reason.value for c in r.candidates or () if c.finish_reason]
    if finish:
        call.finish = finish
    _gemini_usage(call, r.usage_metadata)
    call.text += r.text or ""


class _GeminiStream:
    """A google-genai stream is a plain generator, so the wrapper is one too."""

    def __init__(self, inner: Any, call: _Call) -> None:
        self._inner, self._call = inner, call

    def _closed(self) -> None:
        if "close-leaks-span" not in self._call.faults:
            self._call.end()

    def __iter__(self):
        try:
            for chunk in self._inner:
                _gemini_response(self._call, chunk)
                yield chunk
        except GeneratorExit:
            self._inner.close()
            self._closed()
            raise
        except Exception as exc:
            self._call.fail(exc)
            raise
        self._call.end()

    async def __aiter__(self):
        try:
            async for chunk in self._inner:
                _gemini_response(self._call, chunk)
                yield chunk
        except GeneratorExit:
            await self._inner.aclose()
            self._closed()
            raise
        except Exception as exc:
            self._call.fail(exc)
            raise
        self._call.end()


def _instrument_gemini(tracer: Tracer, faults: set[str]) -> None:
    """Wrap `Models` and `AsyncModels` of google-genai, for the Gemini API.

    The conventions name the provider `gcp.gemini` for the generativelanguage.googleapis.com
    endpoint (`gcp.vertex_ai` is Vertex AI), and the operation `generate_content`:
    model/gen-ai/registry.yaml#L22-L28 and #L650-L653 at the pinned commit.
    """
    import google.genai.models as gm

    def begin(kwargs: dict) -> _Call:
        return _Call(tracer, "gcp.gemini", kwargs.get("model", ""), faults, "generate_content")

    def plain(create):
        @functools.wraps(create)
        def wrapper(self, *args, **kwargs):
            call = begin(kwargs)
            try:
                result = create(self, *args, **kwargs)
            except Exception as exc:
                call.fail(exc)
                raise
            _gemini_response(call, result)
            call.end()
            return result

        return wrapper

    def aplain(create):
        @functools.wraps(create)
        async def wrapper(self, *args, **kwargs):
            call = begin(kwargs)
            try:
                result = await create(self, *args, **kwargs)
            except Exception as exc:
                call.fail(exc)
                raise
            _gemini_response(call, result)
            call.end()
            return result

        return wrapper

    def stream(create):
        @functools.wraps(create)
        def wrapper(self, *args, **kwargs):
            # The SDK sends the request on the first `next()`, so errors surface while iterating.
            return iter(_GeminiStream(create(self, *args, **kwargs), begin(kwargs)))

        return wrapper

    def astream(create):
        @functools.wraps(create)
        async def wrapper(self, *args, **kwargs):
            call = begin(kwargs)
            try:
                inner = await create(self, *args, **kwargs)
            except Exception as exc:
                call.fail(exc)
                raise
            return _GeminiStream(inner, call).__aiter__()

        return wrapper

    gm.Models.generate_content = plain(gm.Models.generate_content)
    gm.Models.generate_content_stream = stream(gm.Models.generate_content_stream)
    gm.AsyncModels.generate_content = aplain(gm.AsyncModels.generate_content)
    gm.AsyncModels.generate_content_stream = astream(gm.AsyncModels.generate_content_stream)


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


def _wrap(tracer: Tracer, provider: str, faults: set[str], on_result, on_event, create, is_async):
    def begin(kwargs: dict) -> _Call:
        return _Call(tracer, provider, kwargs.get("model", ""), faults)

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


def instrument(tracer_provider, faults: set[str] = frozenset()) -> None:
    tracer = tracer_provider.get_tracer("spantruth.reference")
    _instrument_gemini(tracer, set(faults))
    import anthropic.resources.messages as am
    import openai.resources.chat.completions as oc
    import openai.resources.responses as orr

    for cls, is_async in ((oc.Completions, False), (oc.AsyncCompletions, True)):
        cls.create = _wrap(
            tracer, "openai", set(faults), _openai_response, _openai_chunk, cls.create, is_async
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
        )
