"""The app runs an agent with the OpenAI Agents SDK, as its documentation shows first: an `Agent`
with a model object that holds an OpenAI client pointed at the fake provider, run by `Runner`.

A Chat Completions scenario uses `OpenAIChatCompletionsModel`, a Responses scenario
`OpenAIResponsesModel`. The SDK streams only from an event loop (`Runner.run_streamed`), so a sync
stream scenario and its async twin make the same call.
"""

import asyncio

from spantruth.calls import PROMPT

QUESTION = PROMPT[0]["content"]


def _agent(sc, base_url):
    from agents import Agent, OpenAIChatCompletionsModel, OpenAIResponsesModel
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=base_url, api_key="wt-test", max_retries=sc.retries, timeout=10)
    model_class = OpenAIResponsesModel if sc.api == "responses" else OpenAIChatCompletionsModel
    return Agent(name="spantruth", model=model_class(model=sc.request_model, openai_client=client))


def _usage(result):
    """The usage of the last model response, as the app reads it."""
    if not result.raw_responses:
        return None
    u = result.raw_responses[-1].usage
    return {
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "total_tokens": u.total_tokens,
        "cached_tokens": u.input_tokens_details.cached_tokens,
        "reasoning_tokens": u.output_tokens_details.reasoning_tokens,
    }


def _finish(result, seen):
    seen.response_id = result.last_response_id
    seen.usage = _usage(result)


def run(sc, base_url, seen):
    if sc.stream:
        asyncio.run(_stream(sc, base_url, seen))
    elif sc.asynchronous:
        asyncio.run(_run_async(sc, base_url, seen))
    else:
        from agents import Runner

        result = Runner.run_sync(_agent(sc, base_url), QUESTION)
        seen.text = str(result.final_output)
        _finish(result, seen)


async def _run_async(sc, base_url, seen):
    from agents import Runner

    result = await Runner.run(_agent(sc, base_url), QUESTION)
    seen.text = str(result.final_output)
    _finish(result, seen)


async def _stream(sc, base_url, seen):
    from agents import Runner

    result = Runner.run_streamed(_agent(sc, base_url), QUESTION)
    async for event in result.stream_events():
        if event.type != "raw_response_event":
            continue
        seen.events += 1
        if event.data.type == "response.output_text.delta":
            seen.text += event.data.delta
    _finish(result, seen)
