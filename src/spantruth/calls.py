"""The app side of each scenario: how a normal application calls the provider SDK.

Runs inside a subject's virtual environment. Every function returns what the app saw, so the
runner can compare an instrumented run with an uninstrumented one.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from .model import Scenario

PROMPT = [{"role": "user", "content": "What is the capital of France?"}]
OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "parameters": {"type": "object", "properties": {key: {"type": "string"}}},
        },
    }
    for name, key in (("get_weather", "city"), ("get_time", "zone"))
]
ANTHROPIC_TOOLS = [
    {"name": name, "input_schema": {"type": "object", "properties": {key: {"type": "string"}}}}
    for name, key in (("get_weather", "city"), ("get_time", "zone"))
]


class Seen:
    """What the application observed. Compared between the baseline and instrumented runs."""

    def __init__(self) -> None:
        self.text = ""
        self.events = 0
        self.response_id: str | list[str | None] | None = None
        self.usage: dict | list[dict | None] | None = None
        self.calls: list[dict[str, Any]] = []

    def add(self, one: Seen, start_ns: int, end_ns: int) -> None:
        """Record one call of several. Ids and usages become lists, in the order of the plan."""
        self.text += one.text
        self.events += one.events
        self.response_id = [*(self.response_id or []), one.response_id]
        self.usage = [*(self.usage or []), one.usage]
        self.calls.append({"window": [start_ns, end_ns]})

    def as_dict(self) -> dict[str, Any]:
        seen = {
            "text": self.text,
            "events": self.events,
            "response_id": self.response_id,
            "usage": self.usage,
        }
        # When each call ran. The checks match spans to calls by it, and it is never compared.
        return seen | ({"calls": self.calls} if self.calls else {})


def _openai_kwargs(sc: Scenario) -> dict:
    kw: dict = {"model": sc.request_model, "messages": PROMPT}
    if sc.tools:
        kw["tools"] = OPENAI_TOOLS
    if sc.stream:
        kw["stream"] = True
    if sc.stream and sc.stream_usage:
        kw["stream_options"] = {"include_usage": True}
    return kw


def _anthropic_kwargs(sc: Scenario) -> dict:
    kw: dict = {"model": sc.request_model, "max_tokens": 1024, "messages": PROMPT}
    if sc.tools:
        kw["tools"] = ANTHROPIC_TOOLS
    if sc.stream:
        kw["stream"] = True
    return kw


def _openai_result(r: Any, seen: Seen) -> None:
    msg = r.choices[0].message
    seen.text = (msg.content or "") + "".join(t.function.name for t in msg.tool_calls or ())
    seen.response_id, seen.usage = r.id, r.usage.model_dump()


def _openai_chunk(c: Any, seen: Seen) -> None:
    seen.events += 1
    seen.response_id = c.id
    if c.choices and c.choices[0].delta.content:
        seen.text += c.choices[0].delta.content
    if c.choices and c.choices[0].delta.tool_calls:
        seen.text += "".join(
            tc.function.name
            for tc in c.choices[0].delta.tool_calls
            if tc.function and tc.function.name
        )
    if c.usage:
        seen.usage = c.usage.model_dump()


def _responses_event(e: Any, seen: Seen) -> None:
    seen.events += 1
    if e.type == "response.created":
        seen.response_id = e.response.id
    elif e.type == "response.output_text.delta":
        seen.text += e.delta
    elif e.type == "response.completed" and e.response.usage:
        seen.usage = e.response.usage.model_dump()


def _openai_client(sc: Scenario, base_url: str, asynchronous: bool) -> Any:
    """An OpenAI client, or for Azure the AzureOpenAI client with an endpoint and api-version."""
    import openai

    kw = {"api_key": "wt-test", "max_retries": sc.retries, "timeout": 10}
    if sc.provider == "azure":
        from .wire_azure import API_VERSION

        azure = openai.AsyncAzureOpenAI if asynchronous else openai.AzureOpenAI
        return azure(azure_endpoint=base_url, api_version=API_VERSION, **kw)
    return (openai.AsyncOpenAI if asynchronous else openai.OpenAI)(base_url=base_url, **kw)


def _openai_create(client: Any, sc: Scenario):
    """The call for the scenario's API, and how the app reads a stream from it."""
    if sc.api == "responses":
        kw: dict = {"model": sc.request_model, "input": PROMPT, "stream": sc.stream}
        return client.responses.create(**kw), None, _responses_event
    return client.chat.completions.create(**_openai_kwargs(sc)), _openai_result, _openai_chunk


def _anthropic_result(r: Any, seen: Seen) -> None:
    seen.text = "".join(
        b.text if b.type == "text" else b.name if b.type == "tool_use" else "" for b in r.content
    )
    seen.response_id, seen.usage = r.id, r.usage.model_dump()


def _anthropic_event(e: Any, seen: Seen) -> None:
    seen.events += 1
    if e.type == "message_start":
        seen.response_id = e.message.id
    elif e.type == "content_block_delta" and e.delta.type == "text_delta":
        seen.text += e.delta.text


GEMINI_PROMPT = "What is the capital of France?"


def _gemini_client(sc: Scenario, base_url: str):
    """A Gemini API client (not Vertex AI). google-genai retries nothing unless asked to."""
    from google import genai
    from google.genai import types

    return genai.Client(
        api_key="wt-test", http_options=types.HttpOptions(base_url=base_url, timeout=10_000)
    )


def _gemini_result(r: Any, seen: Seen) -> None:
    seen.text = r.text or ""
    seen.response_id = r.response_id
    seen.usage = r.usage_metadata.model_dump(mode="json") if r.usage_metadata else None


def _gemini_chunk(c: Any, seen: Seen) -> None:
    seen.events += 1
    seen.response_id = c.response_id
    seen.text += c.text or ""
    if c.usage_metadata:
        seen.usage = c.usage_metadata.model_dump(mode="json")


def _run_gemini(sc: Scenario, base_url: str, seen: Seen) -> None:
    client = _gemini_client(sc, base_url)  # held: a client that is collected closes its pool
    models = client.models
    kw = {"model": sc.request_model, "contents": GEMINI_PROMPT}
    if not sc.stream:
        _gemini_result(models.generate_content(**kw), seen)
        return
    stream = models.generate_content_stream(**kw)
    _read(stream, sc, seen, _gemini_chunk)
    stream.close()


async def _run_gemini_async(sc: Scenario, base_url: str, seen: Seen) -> None:
    client = _gemini_client(sc, base_url)
    models = client.aio.models
    kw = {"model": sc.request_model, "contents": GEMINI_PROMPT}
    if not sc.stream:
        _gemini_result(await models.generate_content(**kw), seen)
        return
    stream = await models.generate_content_stream(**kw)
    await _aread(stream, sc, seen, _gemini_chunk)
    await stream.aclose()


def run(sc: Scenario, base_url: str, seen: Seen) -> None:
    """Run one scenario the way an application would. Exceptions propagate to the caller."""
    if sc.calls:
        if sc.concurrent:
            asyncio.run(_gather(sc, base_url, seen))
        else:
            _loop(sc, base_url, seen)
        return
    if sc.provider == "gemini":
        if sc.asynchronous:
            asyncio.run(_run_gemini_async(sc, base_url, seen))
        else:
            _run_gemini(sc, base_url, seen)
        return
    if sc.asynchronous:
        asyncio.run(_run_async(sc, base_url, seen))
        return
    if sc.provider in ("openai", "azure"):
        client = _openai_client(sc, base_url, asynchronous=False)
        if sc.entrypoint == "with_streaming_response":
            with client.chat.completions.with_streaming_response.create(
                **_openai_kwargs(sc)
            ) as response:
                stream = response.parse()
                _read(stream, sc, seen, _openai_chunk)
            return
        result, on_result, on_event = _openai_create(client, sc)
    else:
        import anthropic

        client = anthropic.Anthropic(
            base_url=base_url, api_key="wt-test", max_retries=sc.retries, timeout=10
        )
        result = client.messages.create(**_anthropic_kwargs(sc))
        on_result, on_event = _anthropic_result, _anthropic_event
    if not sc.stream:
        on_result(result, seen)
        return
    if sc.with_block:
        with result as stream:
            _read(stream, sc, seen, on_event)
        return
    _read(result, sc, seen, on_event)
    result.close()


def _read(stream: Any, sc: Scenario, seen: Seen, on_event) -> None:
    for event in stream:
        on_event(event, seen)
        if sc.read_events is not None and seen.events >= sc.read_events:
            break


async def _run_async(sc: Scenario, base_url: str, seen: Seen) -> None:
    if sc.provider in ("openai", "azure"):
        client = _openai_client(sc, base_url, asynchronous=True)
        if sc.entrypoint == "with_streaming_response":
            async with client.chat.completions.with_streaming_response.create(
                **_openai_kwargs(sc)
            ) as response:
                stream = await response.parse()
                await _aread(stream, sc, seen, _openai_chunk)
            return
        pending, on_result, on_event = _openai_create(client, sc)
        result = await pending
    else:
        import anthropic

        client = anthropic.AsyncAnthropic(
            base_url=base_url, api_key="wt-test", max_retries=sc.retries, timeout=10
        )
        result = await client.messages.create(**_anthropic_kwargs(sc))
        on_result, on_event = _anthropic_result, _anthropic_event
    if not sc.stream:
        on_result(result, seen)
        return
    if sc.with_block:
        async with result as stream:
            await _aread(stream, sc, seen, on_event)
        return
    await _aread(result, sc, seen, on_event)
    await result.close()


async def _aread(stream: Any, sc: Scenario, seen: Seen, on_event) -> None:
    async for event in stream:
        on_event(event, seen)
        if sc.read_events is not None and seen.events >= sc.read_events:
            break


def _tool_result(name: str) -> str:
    return {"get_weather": "18 C and sunny", "get_time": "14:05"}.get(name, "done")


def _openai_step(r: Any) -> list:
    """What an agent loop appends after an OpenAI response: the message, then each tool result."""
    msg = r.choices[0].message
    return [msg] + [
        {"role": "tool", "tool_call_id": c.id, "content": _tool_result(c.function.name)}
        for c in msg.tool_calls or ()
    ]


def _anthropic_step(r: Any) -> list:
    """What an agent loop appends after an Anthropic response: the reply, then the results."""
    results = [
        {"type": "tool_result", "tool_use_id": b.id, "content": _tool_result(b.name)}
        for b in r.content
        if b.type == "tool_use"
    ]
    return [{"role": "assistant", "content": r.content}, {"role": "user", "content": results}]


def _loop(sc: Scenario, base_url: str, seen: Seen) -> None:
    """An agent loop: call, run the tools the model asked for, and call again with the results.

    It appends the response message itself, as the provider's own tool-use guides show.
    """
    messages: list = list(PROMPT)
    if sc.provider == "openai":
        import openai

        client = openai.OpenAI(base_url=base_url, api_key="wt-test", max_retries=0, timeout=10)
        create, on_result, step = client.chat.completions.create, _openai_result, _openai_step
        extra: dict = {"tools": OPENAI_TOOLS}
    else:
        import anthropic

        client = anthropic.Anthropic(
            base_url=base_url, api_key="wt-test", max_retries=0, timeout=10
        )
        create, on_result, step = client.messages.create, _anthropic_result, _anthropic_step
        extra = {"tools": ANTHROPIC_TOOLS, "max_tokens": 1024}
    for call in sc.calls:
        one, start = Seen(), time.time_ns()
        r = create(model=call.request_model, messages=list(messages), **extra)
        on_result(r, one)
        seen.add(one, start, time.time_ns())
        messages += step(r)


async def _gather(sc: Scenario, base_url: str, seen: Seen) -> None:
    """Concurrent calls on one async client, each a stream, read side by side.

    Each reader yields to the event loop after every event, as an app that forwards each chunk
    does, so the streams interleave and an instrumentation with shared state mixes them up.
    """
    if sc.provider == "openai":
        import openai

        client = openai.AsyncOpenAI(base_url=base_url, api_key="wt-test", max_retries=0, timeout=10)
        create, on_event = client.chat.completions.create, _openai_chunk
        extra: dict = {"stream_options": {"include_usage": True}}
    else:
        import anthropic

        client = anthropic.AsyncAnthropic(
            base_url=base_url, api_key="wt-test", max_retries=0, timeout=10
        )
        create, on_event = client.messages.create, _anthropic_event
        extra = {"max_tokens": 1024}

    async def one(model: str) -> tuple[Seen, int, int]:
        part, start = Seen(), time.time_ns()
        stream = await create(model=model, messages=PROMPT, stream=True, **extra)
        async for event in stream:
            on_event(event, part)
            await asyncio.sleep(0)
        await stream.close()
        return part, start, time.time_ns()

    for part, start, end in await asyncio.gather(*(one(c.request_model) for c in sc.calls)):
        seen.add(part, start, end)
