"""The app calls LiteLLM as its documentation shows first: `litellm.completion` with the provider
as a prefix of the model name, and `api_base` pointed at the fake provider.

LiteLLM answers every provider in the OpenAI shape, so one reader serves both providers. For an
Anthropic response it makes up a new id on every call (`_generate_id` in `litellm/types/utils.py`)
and drops the provider's, so the app records no id there: an id that differs on every call cannot
show whether the instrumentation changed what the app saw.
"""

import asyncio

from spantruth.calls import OPENAI_TOOLS, PROMPT


def _kwargs(sc, base_url):
    kw = {
        "model": f"{sc.provider}/{sc.request_model}",
        "messages": PROMPT,
        "api_base": base_url,
        "api_key": "wt-test",
        # LiteLLM hands this to the OpenAI SDK, which retries.
        "max_retries": sc.retries,
        "timeout": 10,
    }
    if sc.provider == "anthropic":
        kw["max_tokens"] = 1024
    if sc.tools:
        # LiteLLM takes OpenAI tool definitions for every provider and translates them.
        kw["tools"] = OPENAI_TOOLS
    if sc.stream:
        kw["stream"] = True
    if sc.stream and sc.stream_usage:
        kw["stream_options"] = {"include_usage": True}
    return kw


class _Reader:
    def __init__(self, sc, seen):
        self.sc, self.seen = sc, seen

    def _id(self, r):
        return r.id if self.sc.provider == "openai" else None

    def result(self, r):
        msg = r.choices[0].message
        self.seen.text = (msg.content or "") + "".join(
            t.function.name for t in msg.tool_calls or ()
        )
        self.seen.response_id = self._id(r)
        self.seen.usage = r.usage.model_dump() if getattr(r, "usage", None) else None

    def chunk(self, c):
        """Record one chunk. True when the app stops reading here."""
        seen = self.seen
        seen.events += 1
        seen.response_id = self._id(c)
        delta = c.choices[0].delta if c.choices else None
        if delta is not None and delta.content:
            seen.text += delta.content
        if delta is not None and delta.tool_calls:
            seen.text += "".join(
                tc.function.name for tc in delta.tool_calls if tc.function and tc.function.name
            )
        if getattr(c, "usage", None):
            seen.usage = c.usage.model_dump()
        return self.sc.read_events is not None and seen.events >= self.sc.read_events

    def read(self, stream):
        for chunk in stream:
            if self.chunk(chunk):
                break


def run(sc, base_url, seen):
    import litellm

    if sc.asynchronous:
        asyncio.run(_run_async(sc, base_url, seen))
        return
    reader = _Reader(sc, seen)
    result = litellm.completion(**_kwargs(sc, base_url))
    if not sc.stream:
        reader.result(result)
    elif sc.with_block:
        with result as stream:
            reader.read(stream)
    else:
        reader.read(result)
        result.close()


async def _run_async(sc, base_url, seen):
    import litellm

    reader = _Reader(sc, seen)
    result = await litellm.acompletion(**_kwargs(sc, base_url))
    if not sc.stream:
        reader.result(result)
        return
    async for chunk in result:
        if reader.chunk(chunk):
            break
    await result.aclose()
