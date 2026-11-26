"""The app calls a LangChain chat model: `ChatOpenAI` or `ChatAnthropic` with `base_url` pointed
at the fake provider, then `invoke`, `stream`, `ainvoke` or `astream`. A tool scenario binds the
tools with `bind_tools` and makes one model call. The LangChain v1 docs show `init_chat_model`
first. For these providers it builds this same `ChatOpenAI` or `ChatAnthropic` object.

An app that stops reading a stream early leaves the loop and closes the generator, as `calls.py`
closes the SDK stream. A streamed chunk carries no provider id, only LangChain's run id, which
differs on every call, so the app records no id for a stream.
"""

import asyncio

from spantruth.calls import OPENAI_TOOLS, PROMPT


def _model(sc, base_url):
    common = {
        "model": sc.request_model,
        "base_url": base_url,
        "api_key": "wt-test",
        "max_retries": sc.retries,
        "timeout": 10,
    }
    if sc.provider == "openai":
        from langchain_openai import ChatOpenAI

        extra = {"use_responses_api": True} if sc.api == "responses" else {}
        model = ChatOpenAI(stream_usage=sc.stream_usage, **common, **extra)
    else:
        from langchain_anthropic import ChatAnthropic

        model = ChatAnthropic(max_tokens=1024, **common)
    return model.bind_tools(OPENAI_TOOLS) if sc.tools else model


def _text(content):
    """The text of a message: a string, or the text blocks of a list of content blocks."""
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))


class _Reader:
    def __init__(self, sc, seen):
        self.sc, self.seen = sc, seen

    def result(self, msg):
        seen = self.seen
        seen.text = _text(msg.content) + "".join(t["name"] for t in msg.tool_calls)
        seen.response_id = msg.response_metadata.get("id")
        seen.usage = dict(msg.usage_metadata) if msg.usage_metadata else None

    def chunk(self, c):
        """Record one chunk. True when the app stops reading here."""
        seen = self.seen
        seen.events += 1
        seen.text += _text(c.content)
        seen.text += "".join(t["name"] for t in c.tool_call_chunks if t.get("name"))
        seen.response_id = c.response_metadata.get("id") or seen.response_id
        if c.usage_metadata:
            seen.usage = dict(c.usage_metadata)
        return self.sc.read_events is not None and seen.events >= self.sc.read_events


def run(sc, base_url, seen):
    if sc.asynchronous:
        asyncio.run(_run_async(sc, base_url, seen))
        return
    reader = _Reader(sc, seen)
    model = _model(sc, base_url)
    if not sc.stream:
        reader.result(model.invoke(PROMPT))
        return
    stream = model.stream(PROMPT)
    for chunk in stream:
        if reader.chunk(chunk):
            break
    stream.close()


async def _run_async(sc, base_url, seen):
    reader = _Reader(sc, seen)
    model = _model(sc, base_url)
    if not sc.stream:
        reader.result(await model.ainvoke(PROMPT))
        return
    stream = model.astream(PROMPT)
    async for chunk in stream:
        if reader.chunk(chunk):
            break
    await stream.aclose()
