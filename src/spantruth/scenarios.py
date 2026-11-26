"""The scenario registry. Every token count is distinct and non-round on purpose.

A swapped field, a dropped cache count or a wrong sum then shows up as a wrong number,
never as a coincidental pass. Model names are placeholders: no request leaves the machine.
"""

from __future__ import annotations

from dataclasses import replace

from . import recorded
from . import wire_anthropic as an
from . import wire_openai as oa
from .model import Call, Scenario, Truth

OPENAI_MODEL = "gpt-5-mini"
OPENAI_SERVED = "gpt-5-mini-2026-08-07"
# GPT-5.6 and later bill a prompt-cache write and report it, so the cache-write scenarios use one.
OPENAI_CACHE_MODEL = "gpt-5.6-luna"
OPENAI_CACHE_SERVED = "gpt-5.6-luna-2026-10-14"
# An open-weights model behind an OpenAI-compatible server, such as vLLM or Ollama.
COMPAT_MODEL = "qwen3.5-27b"
COMPAT_SERVED = "Qwen/Qwen3.5-27B-Instruct"
ANTHROPIC_MODEL = "claude-sonnet-5-5"
ANTHROPIC_SERVED = "claude-sonnet-5-5-20260915"
PARTS = ("The capital", " of France", " is Paris.")
WEATHER = ("get_weather", {"city": "Paris"})
TIME = ("get_time", {"zone": "Europe/Paris"})

# Async variants run the same fixture through the async client.
ASYNC_PATTERNS = (
    "basic",
    "stream",
    "stream-early-exit",
    "stream-dropped",
    "stream-error-event",
    "stream-helper",
)


def _openai() -> list[Scenario]:
    def sc(name: str, title: str, catches: str, response, **kw) -> Scenario:
        return Scenario(
            id=f"openai/{name}",
            provider="openai",
            title=title,
            catches=catches,
            response=response,
            **{"request_model": OPENAI_MODEL} | kw,
        )

    body = dict(model=OPENAI_SERVED, text="The capital of France is Paris.", finish="stop")
    stream = dict(model=OPENAI_SERVED, parts=PARTS, finish="stop")
    return [
        sc(
            "basic",
            "One call",
            "model, id, finish reason and tokens",
            oa.completion(id="chatcmpl-wt-basic", prompt=1187, completion_tokens=263, **body),
        ),
        sc(
            "stream",
            "Stream read to the end",
            "usage read from the final chunk",
            oa.chunks(id="chatcmpl-wt-stream", prompt=2311, completion_tokens=419, **stream),
            stream=True,
        ),
        sc(
            "stream-early-exit",
            "App closes the stream after two chunks",
            "a span that never ends, a drained response",
            oa.chunks(id="chatcmpl-wt-early", prompt=1913, completion_tokens=347, **stream),
            stream=True,
            read_events=2,
        ),
        sc(
            "cache-hit",
            "Prompt cache hit",
            "cached tokens dropped from usage",
            oa.completion(
                id="chatcmpl-wt-cache", prompt=5379, completion_tokens=377, cached=4096, **body
            ),
        ),
        sc(
            "stream-cache-hit",
            "Prompt cache hit, streamed",
            "cached tokens lost in a stream",
            oa.chunks(
                id="chatcmpl-wt-scache", prompt=7213, completion_tokens=511, cached=6144, **stream
            ),
            stream=True,
        ),
        sc(
            "cache-write",
            "Prompt cache read and write",
            "cache writes dropped from usage",
            oa.completion(
                id="chatcmpl-wt-cwrite",
                prompt=6619,
                completion_tokens=401,
                cached=2753,
                cache_write=3181,
                **body | {"model": OPENAI_CACHE_SERVED},
            ),
            request_model=OPENAI_CACHE_MODEL,
        ),
        sc(
            "stream-cache-write",
            "Prompt cache read and write, streamed",
            "cache writes lost in a stream",
            oa.chunks(
                id="chatcmpl-wt-scwrite",
                prompt=8237,
                completion_tokens=457,
                cached=3967,
                cache_write=3539,
                **stream | {"model": OPENAI_CACHE_SERVED},
            ),
            request_model=OPENAI_CACHE_MODEL,
            stream=True,
        ),
        sc(
            "stream-no-usage",
            "Stream without include_usage",
            "token counts invented when the stream carries none",
            oa.chunks(
                id="chatcmpl-wt-nousage",
                prompt=1667,
                completion_tokens=389,
                include_usage=False,
                **stream,
            ),
            stream=True,
            stream_usage=False,
        ),
        sc(
            "tool-call",
            "Two parallel tool calls",
            "the finish reason of a tool-call response",
            oa.completion(
                id="chatcmpl-wt-tools",
                model=OPENAI_SERVED,
                text=None,
                finish="tool_calls",
                prompt=931,
                completion_tokens=88,
                tool_calls=(
                    ("call_wt_1", WEATHER[0], '{"city":"Paris"}'),
                    ("call_wt_2", TIME[0], '{"zone":"Europe/Paris"}'),
                ),
            ),
            tools=True,
        ),
        sc(
            "stream-tool-call",
            "Two parallel tool calls, streamed",
            "the finish reason of a streamed tool-call response",
            oa.chunks(
                id="chatcmpl-wt-stools",
                model=OPENAI_SERVED,
                parts=(),
                finish="tool_calls",
                prompt=1553,
                completion_tokens=173,
                tool_calls=(
                    ("call_wt_1", WEATHER[0], '{"city":"Paris"}'),
                    ("call_wt_2", TIME[0], '{"zone":"Europe/Paris"}'),
                ),
            ),
            tools=True,
            stream=True,
        ),
        sc(
            "rate-limited",
            "HTTP 429",
            "a failed call recorded as a success",
            oa.error(429, "rate_limit_exceeded", "Rate limit reached for gpt-5-mini."),
        ),
        sc(
            "retry-429",
            "HTTP 429 with retry-after, then a success",
            "an error status or duplicate span on a retried call",
            oa.completion(id="chatcmpl-wt-retry", prompt=2477, completion_tokens=367, **body),
            responses=(
                oa.error(
                    429,
                    "rate_limit_exceeded",
                    "Rate limit reached for gpt-5-mini.",
                    headers={"retry-after": "0.1"},
                ),
                oa.completion(id="chatcmpl-wt-retry", prompt=2477, completion_tokens=367, **body),
            ),
            retries=2,
        ),
        sc(
            "stream-dropped",
            "Connection drops mid-stream",
            "an error swallowed into a cut-off answer",
            oa.chunks(
                id="chatcmpl-wt-drop", prompt=1427, completion_tokens=301, drop_after=3, **stream
            ),
            stream=True,
        ),
        sc(
            "reasoning",
            "Reasoning tokens",
            "reasoning tokens lost from output",
            oa.completion(
                id="chatcmpl-wt-reason", prompt=1601, completion_tokens=2219, reasoning=1792, **body
            ),
        ),
        sc(
            "stream-reasoning",
            "Reasoning tokens, streamed",
            "reasoning tokens lost from output in a stream",
            oa.chunks(
                id="chatcmpl-wt-sreason",
                prompt=2089,
                completion_tokens=2459,
                reasoning=1931,
                **stream,
            ),
            stream=True,
        ),
        sc(
            "stream-helper",
            "Stream via with_streaming_response",
            "nothing recorded when using SDK helper context managers",
            oa.chunks(id="chatcmpl-wt-shelper", prompt=2137, completion_tokens=313, **stream),
            stream=True,
            entrypoint="with_streaming_response",
        ),
    ]


def _compat() -> list[Scenario]:
    """OpenAI-compatible servers, called with the openai SDK: vLLM, Ollama, LiteLLM, Together.

    Their usage leaves out the details objects, or sends null for them. The API reference marks
    both as optional, so the shape is valid, and a library must still report input and output
    tokens and must not report a cache count that the response does not carry.
    """

    def sc(name: str, title: str, catches: str, response, **kw) -> Scenario:
        return Scenario(
            id=f"openai/compat-{name}",
            provider="openai",
            title=title,
            catches=catches,
            request_model=COMPAT_MODEL,
            response=response,
            **kw,
        )

    body = dict(model=COMPAT_SERVED, text="The capital of France is Paris.", finish="stop")
    stream = dict(model=COMPAT_SERVED, parts=PARTS, finish="stop")
    return [
        sc(
            "no-details",
            "OpenAI-compatible server, usage without details",
            "a crash or an invented cache count when prompt_tokens_details is absent",
            oa.completion(
                id="chatcmpl-wt-cnodet",
                prompt=1129,
                completion_tokens=241,
                details="absent",
                **body,
            ),
        ),
        sc(
            "null-details",
            "OpenAI-compatible server, details sent as null",
            "a crash or an invented cache count when prompt_tokens_details is null",
            oa.completion(
                id="chatcmpl-wt-cnull", prompt=1213, completion_tokens=277, details="null", **body
            ),
        ),
        sc(
            "stream-no-details",
            "OpenAI-compatible server, streamed usage without details",
            "usage lost in a stream whose final chunk has no details",
            oa.chunks(
                id="chatcmpl-wt-csnodet",
                prompt=1361,
                completion_tokens=293,
                details="absent",
                **stream,
            ),
            stream=True,
        ),
        sc(
            "stream-usage-on-finish",
            "Usage on the chunk with the finish reason",
            "usage lost when it is not in a chunk of its own",
            oa.chunks(
                id="chatcmpl-wt-csfinish",
                prompt=1489,
                completion_tokens=331,
                cached=1063,
                usage_on_finish=True,
                **stream,
            ),
            stream=True,
        ),
    ]


def _replayed(name: str, path, title: str, catches: str) -> Scenario:
    """An OpenAI-compatible response replayed from a fixture file, tagged with its tier."""
    return Scenario(
        id=f"openai/compat-{name}",
        provider="openai",
        title=title,
        catches=catches,
        request_model=recorded.requested_model(path),
        response=recorded.load(path),
        tags=("recorded", recorded.tier(path)),
    )


def _recorded() -> list[Scenario]:
    """Scenarios replayed from fixture files, rather than built in code.

    A file is the way to send a field that the SDK types do not declare, such as DeepSeek's
    `prompt_cache_hit_tokens`: its provenance says where the shape comes from, so the fixture
    check need not accept invented fields in built fixtures.
    """
    abort = recorded.FIXTURES / "openai-responses" / "stream.missing-finish-abort.json"
    compat = [
        _replayed(
            "partial-details",
            recorded.FIXTURES / "ollama-openai" / "truncation.text.json",
            "Ollama, details with a cache count only",
            "a crash or a lost cache count when the details hold only cached_tokens",
        ),
        _replayed(
            "extra-cache-fields",
            recorded.FIXTURES / "deepseek" / "usage.cached-tokens.json",
            "DeepSeek, cache counts in its own fields too",
            "the standard cache count lost next to provider fields",
        ),
        _replayed(
            "extra-timing-fields",
            recorded.FIXTURES / "groq" / "usage.timings.json",
            "Groq, timings in usage and no details",
            "a crash or an invented count when usage holds timings",
        ),
    ]
    return [
        Scenario(
            id="openai/responses-stream-missing-finish-abort",
            provider="openai",
            api="responses",
            title="Responses API stream cut after eight recorded events",
            catches="an aborted stream recorded as a success",
            request_model=recorded.requested_model(abort),
            response=recorded.load(abort),
            stream=True,
            tags=("recorded",),
        ),
        *compat,
    ]


def _anthropic() -> list[Scenario]:
    def sc(name: str, title: str, catches: str, response, **kw) -> Scenario:
        return Scenario(
            id=f"anthropic/{name}",
            provider="anthropic",
            title=title,
            catches=catches,
            request_model=ANTHROPIC_MODEL,
            response=response,
            **kw,
        )

    body = dict(model=ANTHROPIC_SERVED, text="The capital of France is Paris.", stop="end_turn")
    stream = dict(model=ANTHROPIC_SERVED, parts=PARTS, stop="end_turn")
    return [
        sc(
            "basic",
            "One call",
            "model, id, finish reason and tokens",
            an.message(id="msg_wt_basic", input_tokens=1187, output_tokens=263, **body),
        ),
        sc(
            "stream",
            "Stream read to the end",
            "usage split across message_start and message_delta",
            an.events(id="msg_wt_stream", input_tokens=2311, output_tokens=419, **stream),
            stream=True,
        ),
        sc(
            "stream-early-exit",
            "App closes the stream after two events",
            "a span that never ends, a drained response",
            an.events(id="msg_wt_early", input_tokens=1913, output_tokens=347, **stream),
            stream=True,
            read_events=2,
        ),
        sc(
            "cache-hit",
            "Prompt cache read and write",
            "cache tokens not added to input tokens",
            an.message(
                id="msg_wt_cache",
                input_tokens=1187,
                output_tokens=377,
                cache_read=4096,
                cache_write=519,
                **body,
            ),
        ),
        sc(
            "stream-cache-hit",
            "Prompt cache read and write, streamed",
            "cache counts reset by message_delta",
            an.events(
                id="msg_wt_scache",
                input_tokens=1543,
                output_tokens=511,
                cache_read=6144,
                cache_write=737,
                **stream,
            ),
            stream=True,
        ),
        sc(
            "stream-cache-cumulative",
            "Cache counts repeated in message_delta",
            "cache tokens counted twice",
            an.events(
                id="msg_wt_cumul",
                input_tokens=1279,
                output_tokens=467,
                cache_read=3583,
                cache_write=811,
                cumulative_delta=True,
                **stream,
            ),
            stream=True,
        ),
        sc(
            "cache-1h",
            "Prompt cache write with a 1-hour part",
            "the TTL breakdown added to the cache writes, or only one part of it reported",
            an.message(
                id="msg_wt_cache1h",
                input_tokens=1307,
                output_tokens=389,
                cache_read=2861,
                cache_write=2120,
                cache_write_1h=1663,
                **body,
            ),
        ),
        sc(
            "stream-cache-1h",
            "Prompt cache write with a 1-hour part, streamed",
            "the TTL breakdown in message_start added to the cache writes",
            an.events(
                id="msg_wt_scache1h",
                input_tokens=1451,
                output_tokens=523,
                cache_read=3217,
                cache_write=2492,
                cache_write_1h=1879,
                **stream,
            ),
            stream=True,
        ),
        sc(
            "stream-error-event",
            "Overloaded error event mid-stream",
            "an error event swallowed into a cut-off answer",
            an.events(
                id="msg_wt_overload",
                input_tokens=1301,
                output_tokens=283,
                error_after=3,
                **stream,
            ),
            stream=True,
        ),
        sc(
            "tool-call",
            "Two parallel tool uses",
            "the finish reason of a tool-call response",
            an.message(
                id="msg_wt_tools",
                model=ANTHROPIC_SERVED,
                text=None,
                stop="tool_use",
                input_tokens=931,
                output_tokens=88,
                tool_uses=(("toolu_wt_1", *WEATHER), ("toolu_wt_2", *TIME)),
            ),
            tools=True,
        ),
        sc(
            "stream-tool-call",
            "Two parallel tool uses, streamed",
            "the finish reason of a streamed tool-call response",
            an.events(
                id="msg_wt_stools",
                model=ANTHROPIC_SERVED,
                parts=(),
                stop="tool_use",
                input_tokens=1619,
                output_tokens=197,
                tool_uses=(("toolu_wt_1", *WEATHER), ("toolu_wt_2", *TIME)),
            ),
            tools=True,
            stream=True,
        ),
        sc(
            "rate-limited",
            "HTTP 429",
            "a failed call recorded as a success",
            an.error(429, "rate_limit_error", "Number of request tokens has exceeded your limit."),
        ),
        sc(
            "retry-429",
            "HTTP 429 with retry-after, then a success",
            "an error status or duplicate span on a retried call",
            an.message(id="msg_wt_retry", input_tokens=2477, output_tokens=367, **body),
            responses=(
                an.error(
                    429,
                    "rate_limit_error",
                    "Number of request tokens has exceeded your limit.",
                    headers={"retry-after": "0.1"},
                ),
                an.message(id="msg_wt_retry", input_tokens=2477, output_tokens=367, **body),
            ),
            retries=2,
        ),
        sc(
            "stream-dropped",
            "Connection drops mid-stream",
            "an error swallowed into a cut-off answer",
            an.events(
                id="msg_wt_drop", input_tokens=1427, output_tokens=301, drop_after=3, **stream
            ),
            stream=True,
        ),
        sc(
            "thinking",
            "Extended thinking",
            "thinking tokens lost from output",
            an.message(
                id="msg_wt_thinking",
                input_tokens=1627,
                output_tokens=1273,
                thinking=(
                    "Let me think about the capital of France. The capital of France is Paris."
                ),
                thinking_tokens=947,
                **body,
            ),
        ),
        sc(
            "stream-thinking",
            "Extended thinking, streamed",
            "thinking tokens lost from output in a stream",
            an.events(
                id="msg_wt_sthink",
                input_tokens=1831,
                output_tokens=1439,
                thinking_parts=(
                    "Let me think about",
                    " the capital of France.",
                    " The capital is Paris.",
                ),
                thinking_tokens=1103,
                **stream,
            ),
            stream=True,
        ),
    ]


def _plans() -> list[Scenario]:
    """Scenarios with more than one call in the app's trace. Each call's span has its own counts.

    An agent loop asks for the weather, then for the time, then answers. The second and third
    calls send the tool results back, and read most of their prompt from the cache, as a loop does.
    """
    weather = (("call_wt_w", WEATHER[0], '{"city":"Paris"}'),)
    time_ = (("call_wt_t", TIME[0], '{"zone":"Europe/Paris"}'),)
    answer = "It is 18 C and sunny in Paris, and the time is 14:05."
    tool = dict(model=OPENAI_SERVED, text=None, finish="tool_calls")
    openai_loop = (
        oa.completion(
            id="chatcmpl-wt-loop1", prompt=1171, completion_tokens=97, tool_calls=weather, **tool
        ),
        oa.completion(
            id="chatcmpl-wt-loop2",
            prompt=1307,
            completion_tokens=89,
            cached=1093,
            tool_calls=time_,
            **tool,
        ),
        oa.completion(
            id="chatcmpl-wt-loop3",
            model=OPENAI_SERVED,
            text=answer,
            finish="stop",
            prompt=1459,
            completion_tokens=211,
            cached=1279,
        ),
    )
    use = dict(model=ANTHROPIC_SERVED, text=None, stop="tool_use")
    anthropic_loop = (
        an.message(
            id="msg_wt_loop1",
            input_tokens=1171,
            output_tokens=97,
            tool_uses=(("toolu_wt_w", *WEATHER),),
            **use,
        ),
        an.message(
            id="msg_wt_loop2",
            input_tokens=214,
            output_tokens=89,
            cache_read=1093,
            tool_uses=(("toolu_wt_t", *TIME),),
            **use,
        ),
        an.message(
            id="msg_wt_loop3",
            model=ANTHROPIC_SERVED,
            text=answer,
            stop="end_turn",
            input_tokens=180,
            output_tokens=211,
            cache_read=1279,
        ),
    )

    def loop(provider: str, model: str, responses: tuple) -> Scenario:
        return Scenario(
            id=f"{provider}/agent-loop",
            provider=provider,
            title="Agent loop: three calls, two with tool results",
            catches="one call's counts on another call's span, or a call with no span",
            request_model=model,
            response=responses[0],
            tools=True,
            calls=tuple(Call(model, r) for r in responses),
        )

    return [
        loop("openai", OPENAI_MODEL, openai_loop),
        loop("anthropic", ANTHROPIC_MODEL, anthropic_loop),
    ]


def _with_block(scenarios: list[Scenario]) -> list[Scenario]:
    """The early exit again, leaving a `with` block instead of calling `close()`."""
    extra = []
    for s in scenarios:
        provider, name = s.id.split("/", 1)
        if name == "stream-early-exit":
            extra.append(
                replace(
                    s,
                    id=f"{provider}/stream-early-exit-with",
                    with_block=True,
                    title=f"{s.title.replace('closes', 'leaves')} with a with block",
                )
            )
    return scenarios + extra


def _with_async(scenarios: list[Scenario]) -> list[Scenario]:
    extra = []
    for s in scenarios:
        provider, name = s.id.split("/", 1)
        if name in ASYNC_PATTERNS:
            extra.append(
                replace(
                    s, id=f"{provider}/async-{name}", asynchronous=True, title=f"{s.title} (async)"
                )
            )
    return scenarios + extra


# Content capture: the basic call and the full stream again, with the library's own switch for
# recording prompt and completion text set off, and set on. The other scenarios leave it alone.
CONTENT_PATTERNS = {"basic": "content", "stream": "stream-content"}


def _with_content(scenarios: list[Scenario]) -> list[Scenario]:
    extra = []
    for s in scenarios:
        provider, name = s.id.split("/", 1)
        if name in CONTENT_PATTERNS:
            for mode in ("off", "on"):
                extra.append(
                    replace(
                        s,
                        id=f"{provider}/{CONTENT_PATTERNS[name]}-{mode}",
                        content=mode,
                        title=f"{s.title}, content capture switched {mode}",
                        catches="prompt and completion text recorded against the switch"
                        if mode == "off"
                        else "completion text that differs from what the app read",
                    )
                )
    return scenarios + extra


SCENARIOS: dict[str, Scenario] = {
    s.id: s
    for s in _with_content(_with_async(_with_block(_openai() + _anthropic())))
    + _compat()
    + _recorded()
    + _plans()
}


def depends_on(scenario_id: str) -> str | None:
    """The scenario whose cell decides this one's verdict: an early exit depends on its full stream.

    When a library records no span for the full stream, an early exit is graded as the same gap
    (`missing`), not as a span lost by the exit. See runner._downgrade_unsupported_streams.
    """
    if "early-exit" not in scenario_id:
        return None
    full = scenario_id.replace("-early-exit", "").removesuffix("-with")
    return full if full in SCENARIOS else None


def with_dependencies(ids: list[str]) -> list[str]:
    """The selected scenarios, each once, followed by the scenarios their verdicts depend on."""
    out = list(dict.fromkeys(ids))
    for sid in list(out):
        if (needed := depends_on(sid)) and needed not in out:
            out.append(needed)
    return out


def get(scenario_id: str) -> Scenario:
    try:
        return SCENARIOS[scenario_id]
    except KeyError:
        raise KeyError(f"unknown scenario {scenario_id!r}; run `spantruth list`") from None


def by_key(key: str) -> Scenario:
    return get(key.replace("--", "/"))


def truth(scenario: Scenario) -> Truth:
    if scenario.calls:
        one = [
            replace(
                scenario, calls=(), responses=(), request_model=c.request_model, response=c.response
            )
            for c in scenario.calls
        ]
        return Truth(request_model=scenario.request_model, calls=tuple(truth(c) for c in one))
    if scenario.api == "responses":
        derive = oa.responses_truth
    else:
        derive = oa.truth if scenario.provider == "openai" else an.truth
    return derive(scenario.request_model, scenario.response, scenario.read_events)
