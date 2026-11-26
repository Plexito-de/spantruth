"""The app calls a Pydantic AI agent, as its documentation shows first: an `Agent` over an
`OpenAIChatModel` or an `AnthropicModel`, with a provider pointed at the fake provider.

`run_sync` and `run` for one answer, `run_stream_sync` and `run_stream` for a stream. The app reads
a stream as text deltas without debouncing, so the number of events it sees does not depend on
timing.
"""

import asyncio

from spantruth.calls import PROMPT

QUESTION = PROMPT[0]["content"]


def _agent(sc, base_url):
    from pydantic_ai import Agent

    settings = {"max_retries": sc.retries, "timeout": 10}
    if sc.provider == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider(base_url=base_url, api_key="wt-test")
        model = AnthropicModel(sc.request_model, provider=provider)
        settings["max_tokens"] = 1024
    else:
        from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
        from pydantic_ai.providers.openai import OpenAIProvider

        provider = OpenAIProvider(base_url=base_url, api_key="wt-test")
        kind = OpenAIResponsesModel if sc.api == "responses" else OpenAIChatModel
        model = kind(sc.request_model, provider=provider)
    return Agent(model, model_settings=settings)


def _finish(result, seen):
    """What the app reads once the run is over: the id the provider gave, and the usage."""
    seen.response_id = result.response.provider_response_id
    seen.usage = _usage(result.usage)


def _usage(usage):
    """The run's usage as JSON. The cost is a Decimal, so it is kept as its text."""
    from dataclasses import asdict
    from decimal import Decimal

    return {k: str(v) if isinstance(v, Decimal) else v for k, v in asdict(usage).items() if v}


def _done(sc, seen):
    return sc.read_events is not None and seen.events >= sc.read_events


def run(sc, base_url, seen):
    if sc.asynchronous:
        asyncio.run(_run_async(sc, base_url, seen))
        return
    agent = _agent(sc, base_url)
    if not sc.stream:
        result = agent.run_sync(QUESTION)
        seen.text = result.output
        _finish(result, seen)
        return
    # A `with` block, as the documentation of `run_stream_sync` says, so the run is closed here.
    with agent.run_stream_sync(QUESTION) as result:
        for delta in result.stream_text(delta=True, debounce_by=None):
            seen.events += 1
            seen.text += delta
            if _done(sc, seen):
                return
        _finish(result, seen)


async def _run_async(sc, base_url, seen):
    agent = _agent(sc, base_url)
    if not sc.stream:
        result = await agent.run(QUESTION)
        seen.text = result.output
        _finish(result, seen)
        return
    async with agent.run_stream(QUESTION) as result:
        async for delta in result.stream_text(delta=True, debounce_by=None):
            seen.events += 1
            seen.text += delta
            if _done(sc, seen):
                return
        _finish(result, seen)
