"""The app runs an agent with the OpenAI Agents SDK, as its documentation shows first: an `Agent`
with a model object that holds an OpenAI client pointed at the fake provider, run by `Runner`.

A Chat Completions scenario uses `OpenAIChatCompletionsModel`, a Responses scenario
`OpenAIResponsesModel`. The SDK streams only from an event loop (`Runner.run_streamed`), so a sync
stream scenario and its async twin make the same call.

A tool scenario gives the agent the two tools of `calls.py` and
`tool_use_behavior="stop_on_first_tool"`, so the run ends after the model's first tool call
instead of calling the model again. Like `calls.py`, the app records the names of the tools the
model called.
"""

import asyncio

from spantruth.calls import PROMPT

QUESTION = PROMPT[0]["content"]


def _tools():
    from agents import function_tool

    @function_tool
    def get_weather(city: str) -> str:
        return "get_weather"

    @function_tool
    def get_time(zone: str) -> str:
        return "get_time"

    return [get_weather, get_time]


def _agent(sc, base_url):
    from agents import Agent, OpenAIChatCompletionsModel, OpenAIResponsesModel
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=base_url, api_key="wt-test", max_retries=sc.retries, timeout=10)
    model_class = OpenAIResponsesModel if sc.api == "responses" else OpenAIChatCompletionsModel
    model = model_class(model=sc.request_model, openai_client=client)
    if sc.tools:
        return Agent(
            name="spantruth",
            model=model,
            tools=_tools(),
            tool_use_behavior="stop_on_first_tool",
        )
    return Agent(name="spantruth", model=model)


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


def _tool_names(result):
    return "".join(item.raw_item.name for item in result.new_items if item.type == "tool_call_item")


def _finish(result, seen, text=None):
    if text is None:
        text = "" if result.final_output is None else str(result.final_output)
    seen.text = (
        _tool_names(result)
        if any(item.type == "tool_call_item" for item in result.new_items)
        else text
    )
    seen.response_id = result.last_response_id
    seen.usage = _usage(result)


def run(sc, base_url, seen):
    if sc.stream:
        asyncio.run(_stream(sc, base_url, seen))
    elif sc.asynchronous:
        asyncio.run(_run_async(sc, base_url, seen))
    else:
        from agents import Runner

        _finish(Runner.run_sync(_agent(sc, base_url), QUESTION), seen)


async def _run_async(sc, base_url, seen):
    from agents import Runner

    _finish(await Runner.run(_agent(sc, base_url), QUESTION), seen)


async def _stream(sc, base_url, seen):
    from agents import Runner

    result = Runner.run_streamed(_agent(sc, base_url), QUESTION)
    text = ""
    async for event in result.stream_events():
        if event.type != "raw_response_event":
            continue
        seen.events += 1
        if event.data.type == "response.output_text.delta":
            text += event.data.delta
    _finish(result, seen, text)
