"""Fixtures are valid wire data, and the truth derived from them is what the spec says."""

import json
import re

import anthropic.types
import google.genai.types
import openai.resources.chat.completions as oc
import openai.types.chat
import openai.types.responses
import pydantic
import pytest

from spantruth import calls, impact, recorded, scenarios
from spantruth import wire_anthropic as an
from spantruth import wire_azure as az
from spantruth import wire_openai as oa
from spantruth.mockserver import MockServer
from spantruth.model import Event, Response

ALL = list(scenarios.SCENARIOS.values())
# A recording is a real provider stream, and the provider can be ahead of the SDK's types.
RECORDED = {s.id for s in scenarios._recorded()}
ANTHROPIC_EVENT = pydantic.TypeAdapter(anthropic.types.RawMessageStreamEvent)
RESPONSES_EVENT = pydantic.TypeAdapter(openai.types.responses.ResponseStreamEvent)


def _undeclared(parsed, path=""):
    """The fields of a parsed fixture that its SDK type does not declare, as dotted paths.

    The SDK types accept a field they do not know and keep it aside. So a fixture with an invented
    field still parses, and only this list shows it.
    """
    found = []
    if isinstance(parsed, pydantic.BaseModel):
        found += [f"{path}{name}" for name in parsed.model_extra or {}]
        for name in parsed.model_fields_set:
            found += _undeclared(getattr(parsed, name, None), f"{path}{name}.")
    elif isinstance(parsed, (list, tuple)):
        for index, item in enumerate(parsed):
            found += _undeclared(item, f"{path}{index}.")
    elif isinstance(parsed, dict):
        for name, item in parsed.items():
            found += _undeclared(item, f"{path}{name}.")
    return sorted(found)


@pytest.mark.parametrize("sc", ALL, ids=lambda s: s.id)
def test_fixtures_parse_with_sdk_types(sc):
    parsed = []
    for r in sc.responses:
        if r.status >= 400:
            assert r.body is not None and "error" in r.body
            continue
        if sc.api == "responses":
            for e in r.events or ():
                parsed.append(RESPONSES_EVENT.validate_python(json.loads(e.data)))
                assert parsed[-1].type == e.event
        elif sc.provider == "gemini":
            # extra="forbid" on the SDK's types: an undeclared field raises here. The API sends
            # `usageMetadata.serviceTier`, which the SDK's usage type lacks and its parser drops.
            for data in [r.body] if r.body is not None else [e.data for e in r.events or ()]:
                usage = {k: v for k, v in data["usageMetadata"].items() if k != "serviceTier"}
                typed = data | {"usageMetadata": usage}
                parsed.append(google.genai.types.GenerateContentResponse.model_validate(typed))
        elif sc.provider in ("openai", "azure"):
            if r.body is not None:
                parsed.append(openai.types.chat.ChatCompletion.model_validate(r.body, strict=False))
            for e in r.events or ():
                if sc.provider == "azure" and isinstance(e.data, dict) and e.data["id"] == "":
                    # Azure's prompt filter chunk: `object` is empty, which the chunk type
                    # rejects. The SDK does not validate, so the app receives it as sent.
                    assert e.data["choices"] == [] and e.data["prompt_filter_results"]
                    continue
                if isinstance(e.data, dict):
                    parsed.append(openai.types.chat.ChatCompletionChunk.model_validate(e.data))
                else:
                    assert e.data == "[DONE]"
        else:
            if r.body is not None:
                parsed.append(anthropic.types.Message.model_validate(r.body))
            for e in r.events or ():
                if e.event == "error":
                    # Not a RawMessageStreamEvent: the SDK raises on it before parsing.
                    assert e.data["error"]["type"] in {"overloaded_error", "api_error"}
                    continue
                parsed.append(ANTHROPIC_EVENT.validate_python(e.data))
                assert parsed[-1].type == e.event
    if sc.id not in RECORDED:
        allowed = az.AZURE_ONLY if sc.provider == "azure" else frozenset()
        undeclared = [
            p for p in _undeclared(parsed) if re.sub(r"(^|\.)\d+\.", r"\1", p) not in allowed
        ]
        assert undeclared == [], f"{sc.id} sends fields the SDK types do not declare"


def test_the_fixture_check_sees_a_field_the_sdk_type_does_not_declare():
    body = scenarios.get("anthropic/cache-1h").response.body
    ttl = body["usage"]["cache_creation"] | {"ephemeral_2h_input_tokens": 1}
    invented = body | {"usage": body["usage"] | {"cache_creation": ttl}}
    parsed = anthropic.types.Message.model_validate(invented)
    assert _undeclared(parsed) == ["usage.cache_creation.ephemeral_2h_input_tokens"]


def test_a_cumulative_stream_keeps_the_ttl_breakdown_in_message_start():
    # `message_delta` repeats the counts, but its usage type has no `cache_creation`.
    response = an.events(
        id="msg_wt_probe",
        model="claude-sonnet-5-5-20260915",
        parts=("Paris.",),
        stop="end_turn",
        input_tokens=1009,
        output_tokens=211,
        cache_read=307,
        cache_write=401,
        cache_write_1h=283,
        cumulative_delta=True,
    )
    for e in response.events:
        assert _undeclared(ANTHROPIC_EVENT.validate_python(e.data)) == [], e.event
    start = response.events[0].data["message"]["usage"]
    assert start["cache_creation"] == {
        "ephemeral_5m_input_tokens": 118,
        "ephemeral_1h_input_tokens": 283,
    }
    delta = next(e.data for e in response.events if e.event == "message_delta")
    assert delta["usage"]["cache_creation_input_tokens"] == 401


@pytest.mark.parametrize("long_part", [-1, 402])
def test_the_one_hour_part_of_a_fixture_stays_inside_the_cache_write(long_part):
    with pytest.raises(ValueError, match="cache_write"):
        an.message(
            id="msg_wt_probe",
            model="claude-sonnet-5-5-20260915",
            text="Paris.",
            stop="end_turn",
            input_tokens=1009,
            output_tokens=211,
            cache_write=401,
            cache_write_1h=long_part,
        )


@pytest.mark.parametrize("sc", ALL, ids=lambda s: s.id)
def test_fixture_truth_values_are_distinct(sc):
    t = scenarios.truth(sc)
    values = [
        v
        for v in (
            t.input_tokens,
            t.output_tokens,
            t.cache_read_tokens,
            t.cache_write_tokens,
            t.reasoning_tokens,
        )
        if v is not None
    ]
    long_write = t.cache_write_long_tokens
    if long_write and long_write != t.cache_write_tokens:
        # The two parts of a cache write. A library can report one of them alone.
        values += [long_write, t.cache_write_tokens - long_write]
    assert len(values) == len(set(values)), f"{sc.id} repeats a token value: {values}"
    if t.response_model is not None and sc.id not in RECORDED and sc.provider != "gemini":
        # A real server can echo the requested name, so only built fixtures keep the two apart.
        # Gemini is exempt: its API returns `modelVersion` equal to the requested model id, and an
        # invented version would fail libraries that copy the request model, as the API allows.
        assert t.response_model != t.request_model


def test_anthropic_input_tokens_include_cache_read_and_write():
    t = scenarios.truth(scenarios.get("anthropic/cache-hit"))
    assert (t.input_tokens, t.cache_read_tokens, t.cache_write_tokens) == (5802, 4096, 519)


def test_cumulative_delta_counts_cache_once():
    t = scenarios.truth(scenarios.get("anthropic/stream-cache-cumulative"))
    assert (t.input_tokens, t.output_tokens) == (5673, 467)


def test_error_event_and_missing_usage():
    assert scenarios.truth(scenarios.get("anthropic/stream-error-event")).error == (
        "error event: overloaded_error"
    )
    t = scenarios.truth(scenarios.get("openai/stream-no-usage"))
    assert t.input_tokens is None and t.finish_reasons == ("stop",)


def test_anthropic_stream_takes_cache_from_message_start_and_output_from_delta():
    t = scenarios.truth(scenarios.get("anthropic/stream-cache-hit"))
    assert (t.input_tokens, t.output_tokens) == (8424, 511)
    assert (t.cache_read_tokens, t.cache_write_tokens) == (6144, 737)
    assert t.finish_reasons == ("end_turn",)


def test_openai_prompt_tokens_already_include_cached_tokens():
    t = scenarios.truth(scenarios.get("openai/stream-cache-hit"))
    assert (t.input_tokens, t.cache_read_tokens, t.output_tokens) == (7213, 6144, 511)


@pytest.mark.parametrize(
    ("sid", "output", "reasoning"),
    [
        ("openai/reasoning", 2219, 1792),
        ("openai/stream-reasoning", 2459, 1931),
    ],
)
def test_openai_reasoning_stays_inside_output_tokens(sid, output, reasoning):
    sc = scenarios.get(sid)
    t = scenarios.truth(sc)
    assert (t.output_tokens, t.reasoning_tokens) == (output, reasoning)
    assert t.output_tokens > t.reasoning_tokens
    assert t.cache_read_tokens is None and t.cache_write_tokens is None


def test_openai_early_exit_holds_no_usage_and_marks_the_unread_counts():
    t = scenarios.truth(scenarios.get("openai/stream-early-exit"))
    assert t.response_model is not None and t.response_id is not None
    assert t.input_tokens is None and t.output_tokens is None and t.finish_reasons is None
    assert set(t.unread_counts) == {1913, 347}


def test_anthropic_early_exit_knows_input_from_message_start_only():
    t = scenarios.truth(scenarios.get("anthropic/stream-early-exit"))
    assert (t.input_tokens, t.output_tokens, t.finish_reasons) == (1913, None, None)
    assert t.unread_counts == (347,)


def test_openai_stream_without_usage_is_flagged():
    assert scenarios.truth(scenarios.get("openai/stream-no-usage")).no_usage


@pytest.mark.parametrize("sid", ["openai/stream-dropped", "anthropic/stream-dropped"])
def test_dropped_stream_is_an_error_with_no_values(sid):
    t = scenarios.truth(scenarios.get(sid))
    assert t.error == "connection dropped"
    assert t.output_tokens is None and t.finish_reasons is None


def test_error_truth_is_the_status():
    t = scenarios.truth(scenarios.get("openai/rate-limited"))
    assert t.error == "HTTP 429" and t.input_tokens is None


def test_zero_cache_counts_are_not_graded():
    t = scenarios.truth(scenarios.get("openai/basic"))
    assert t.cache_read_tokens is None and t.reasoning_tokens is None


def test_response_rejects_body_and_events_together():
    with pytest.raises(ValueError):
        Response(body={}, events=())


def test_async_variants_exist_for_stream_patterns():
    assert scenarios.get("openai/async-stream-early-exit").asynchronous
    assert scenarios.get("anthropic/async-stream").asynchronous


@pytest.mark.parametrize("sid", ["openai/retry-429", "anthropic/retry-429"])
def test_retry_truth_is_the_successful_call(sid):
    sc = scenarios.get(sid)
    assert len(sc.responses) == 2
    assert sc.responses[0].status == 429
    assert sc.responses[0].headers.get("retry-after") == "0.1"
    assert sc.responses[1].status == 200
    assert sc.retries == 2
    t = scenarios.truth(sc)
    assert t.error is None
    assert (t.input_tokens, t.output_tokens) == (2477, 367)
    assert t.finish_reasons in (("stop",), ("end_turn",))


@pytest.mark.parametrize("sid", ["anthropic/thinking", "anthropic/stream-thinking"])
def test_anthropic_thinking_stays_inside_output_tokens(sid):
    sc = scenarios.get(sid)
    t = scenarios.truth(sc)
    assert t.output_tokens is not None and t.reasoning_tokens is not None
    assert t.output_tokens > t.reasoning_tokens
    assert t.cache_read_tokens is None and t.cache_write_tokens is None


@pytest.mark.parametrize(
    ("sid", "expected_finish", "expected_tokens"),
    [
        ("openai/stream-tool-call", ("tool_calls",), (1553, 173)),
        ("anthropic/stream-tool-call", ("tool_use",), (1619, 197)),
    ],
)
def test_stream_tool_calls_truth(sid, expected_finish, expected_tokens):
    sc = scenarios.get(sid)
    t = scenarios.truth(sc)
    assert t.finish_reasons == expected_finish
    assert (t.input_tokens, t.output_tokens) == expected_tokens
    assert sc.tools and sc.stream


@pytest.mark.parametrize("sid", ["openai/stream-helper", "openai/async-stream-helper"])
def test_stream_helper_truth(sid):
    sc = scenarios.get(sid)
    assert sc.entrypoint == "with_streaming_response"
    t = scenarios.truth(sc)
    assert (t.input_tokens, t.output_tokens) == (2137, 313)
    assert t.finish_reasons == ("stop",)


@pytest.mark.parametrize(
    ("sid", "target_cls"),
    [
        ("openai/stream-helper", oc.Completions),
        ("openai/async-stream-helper", oc.AsyncCompletions),
    ],
)
def test_stream_helper_goes_through_with_streaming_response(monkeypatch, sid, target_cls):
    sc = scenarios.get(sid)
    sent = []
    real = target_cls.create

    def spy(self, *args, **kwargs):
        sent.append((kwargs.get("extra_headers") or {}).get("X-Stainless-Raw-Response"))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(target_cls, "create", spy)
    with MockServer() as mock:
        calls.run(sc, mock.base_url(sc), calls.Seen())
    assert sent == ["stream"]


@pytest.mark.parametrize(
    ("sid", "tokens"),
    [
        ("anthropic/cache-1h", (6288, 2861, 2120, 1663)),
        ("anthropic/stream-cache-1h", (7160, 3217, 2492, 1879)),
    ],
)
def test_anthropic_truth_reads_the_one_hour_share_of_cache_writes(sid, tokens):
    t = scenarios.truth(scenarios.get(sid))
    assert (
        t.input_tokens,
        t.cache_read_tokens,
        t.cache_write_tokens,
        t.cache_write_long_tokens,
    ) == tokens


def test_a_fixture_without_a_ttl_breakdown_has_no_one_hour_writes():
    assert scenarios.truth(scenarios.get("anthropic/cache-hit")).cache_write_long_tokens is None


def _billed(response):
    """Every object of a fixture that holds a `usage`: a body, an event, or an event's message."""
    if response.body is not None:
        yield response.body
    for e in response.events or ():
        data = e.data
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:  # OpenAI ends a stream with the plain text `[DONE]`
                continue
        if isinstance(data, dict):
            # Anthropic's message_start holds a message, a Responses event holds a response.
            yield from (data, data.get("message") or {}, data.get("response") or {})


@pytest.mark.parametrize("sc", ALL, ids=lambda s: s.id)
def test_no_fixture_sends_a_field_the_cost_figure_does_not_price(sc):
    holders = [
        h
        for r in sc.responses
        for h in _billed(r)
        if isinstance(impact.usage_of(sc.provider, h), dict)
    ]
    if scenarios.truth(sc).input_tokens is not None:
        assert holders, f"{sc.id} has token counts, but this test found no usage to read"
    if sc.id in RECORDED and sc.request_model not in impact.load_prices():
        # A replayed body from a server with no list price here: impact.cell_cost gives no cost
        # figure for its model, so a field it does not price cannot make a figure wrong.
        return
    for holder in holders:
        assert impact.unpriced(sc.provider, holder) == [], (
            f"{sc.id} sends fields that change the price and that impact.py does not price. "
            "Decide what each one does to a cost figure, then teach impact.py."
        )


def test_the_price_guard_reads_a_usage_inside_json_text():
    # A recorded fixture keeps its events as JSON text. A guard that skipped text would pass on
    # any recorded stream, whatever its tier.
    completed = {
        "type": "response.completed",
        "response": {"service_tier": "priority", "usage": {"input_tokens": 9, "output_tokens": 3}},
    }
    response = Response(events=(Event(json.dumps(completed), event="response.completed"),))
    found = [impact.unpriced("openai", h) for h in _billed(response)]
    assert ["service_tier='priority'"] in found


@pytest.mark.parametrize(
    ("sid", "tokens"),
    [
        ("openai/cache-write", (6619, 2753, 3181)),
        ("openai/stream-cache-write", (8237, 3967, 3539)),
    ],
)
def test_openai_prompt_tokens_include_cache_reads_and_writes(sid, tokens):
    t = scenarios.truth(scenarios.get(sid))
    assert (t.input_tokens, t.cache_read_tokens, t.cache_write_tokens) == tokens
    assert t.cache_write_long_tokens is None  # Chat Completions has no 12-hour lifetime
    assert scenarios.get(sid).request_model == "gpt-5.6-luna"


def test_an_openai_fixture_without_cache_writes_has_none():
    assert scenarios.truth(scenarios.get("openai/cache-hit")).cache_write_tokens is None


def _usage_of(sid):
    r = scenarios.get(sid).response
    if r.body is not None:
        return r.body["usage"]
    return next(e.data["usage"] for e in r.events if isinstance(e.data, dict) and e.data["usage"])


def test_a_compatible_server_can_leave_out_the_details():
    usage = _usage_of("openai/compat-no-details")
    assert set(usage) == {"prompt_tokens", "completion_tokens", "total_tokens"}
    assert set(_usage_of("openai/compat-stream-no-details")) == set(usage)
    nulls = _usage_of("openai/compat-null-details")
    assert nulls["prompt_tokens_details"] is None and nulls["completion_tokens_details"] is None


@pytest.mark.parametrize(
    "sid",
    ["openai/compat-no-details", "openai/compat-null-details", "openai/compat-stream-no-details"],
)
def test_a_usage_without_details_has_no_cache_or_reasoning_count(sid):
    t = scenarios.truth(scenarios.get(sid))
    assert t.input_tokens and t.output_tokens
    assert t.cache_read_tokens is None and t.reasoning_tokens is None
    assert t.uncounted == ("cache_read_tokens", "reasoning_tokens")


def test_a_zero_count_is_not_an_absent_count():
    # OpenAI sends `cached_tokens: 0`: zero is a count, so a span that reports a cache read
    # is wrong for another reason than invention, and is not graded here.
    assert scenarios.truth(scenarios.get("openai/basic")).uncounted == ()


def test_partial_details_count_only_what_they_hold():
    t = scenarios.truth(scenarios.get("openai/compat-partial-details"))
    assert (t.input_tokens, t.cache_read_tokens, t.output_tokens) == (32, 28, 16)
    assert t.uncounted == ("reasoning_tokens",)


def test_a_usage_without_details_refuses_cache_counts():
    with pytest.raises(ValueError, match="details"):
        oa.completion(
            id="x",
            model="m",
            text="t",
            finish="stop",
            prompt=3,
            completion_tokens=2,
            cached=1,
            details="absent",
        )


def test_usage_on_the_finish_chunk_replaces_the_usage_chunk():
    events = [e.data for e in scenarios.get("openai/compat-stream-usage-on-finish").response.events]
    chunks = [e for e in events if isinstance(e, dict)]
    with_usage = [c for c in chunks if c["usage"]]
    assert len(with_usage) == 1 and with_usage[0]["choices"][0]["finish_reason"] == "stop"
    assert all(c["choices"] for c in chunks)
    t = scenarios.truth(scenarios.get("openai/compat-stream-usage-on-finish"))
    assert (t.input_tokens, t.output_tokens, t.cache_read_tokens) == (1489, 331, 1063)


@pytest.mark.parametrize(
    ("sid", "undeclared"),
    [
        ("openai/compat-partial-details", []),
        (
            "openai/compat-extra-cache-fields",
            ["usage.prompt_cache_hit_tokens", "usage.prompt_cache_miss_tokens"],
        ),
        (
            "openai/compat-extra-timing-fields",
            [
                "usage.completion_time",
                "usage.prompt_time",
                "usage.queue_time",
                "usage.total_time",
                "x_groq",
            ],
        ),
    ],
)
def test_a_replayed_compatible_body_sends_exactly_its_documented_extra_fields(sid, undeclared):
    # These scenarios are exempt from the undeclared-field check because their bytes come from a
    # file with provenance. This pins what the exemption lets through, field by field.
    body = scenarios.get(sid).response.body
    parsed = openai.types.chat.ChatCompletion.model_validate(body)
    assert _undeclared(parsed) == undeclared


def test_an_extra_cache_field_does_not_replace_the_standard_count():
    t = scenarios.truth(scenarios.get("openai/compat-extra-cache-fields"))
    assert (t.input_tokens, t.cache_read_tokens, t.output_tokens) == (1931, 1408, 397)


def test_every_fixture_file_names_its_source():
    for path in recorded.FIXTURES.rglob("*.json"):
        doc = json.loads(path.read_text())
        prov = doc["provenance"]
        if doc["tier"] == "documented":
            assert prov["source_url"].startswith("https://") and prov["notes"], path
        elif doc["tier"] == "recorded":
            assert prov["recorded_at"] and len(prov["raw_sha256"]) == 64, path
        else:
            assert doc["derived_from"]["evidence_url"].startswith("https://"), path


PLANS = [s for s in ALL if s.calls]


@pytest.mark.parametrize("sc", PLANS, ids=lambda s: s.id)
def test_every_count_of_a_plan_is_distinct_across_its_calls(sc):
    # A count that crossed from one call's span to another's then shows as a wrong number.
    t = scenarios.truth(sc)
    assert len(t.calls) == len(sc.calls) and t.input_tokens is None
    values = [
        getattr(c, fact)
        for c in t.calls
        for fact in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
        if getattr(c, fact) is not None
    ]
    assert len(values) == len(set(values)), values
    assert len({c.response_id for c in t.calls}) == len(t.calls)


@pytest.mark.parametrize("sid", ["openai/agent-loop", "anthropic/agent-loop"])
def test_the_agent_loop_sends_each_call_the_tool_results_before_it(sid):
    sc = scenarios.get(sid)
    seen = calls.Seen()
    with MockServer() as mock:
        calls.run(sc, mock.base_url(sc), seen)
        bodies = [json.loads(body) for _, _, body in mock.requests]
    assert seen.response_id == [c.response_id for c in scenarios.truth(sc).calls]
    assert [len(b["messages"]) for b in bodies] == [1, 3, 5]
    results = [m for m in bodies[2]["messages"] if m["role"] in ("tool", "user")]
    assert len(results) == 3  # the prompt and one tool result per earlier call
    assert len(seen.calls) == 3 and all(a <= b for a, b in (c["window"] for c in seen.calls))


def test_the_mock_refuses_a_request_that_no_planned_call_fits():
    sc = scenarios.get("openai/agent-loop")
    with MockServer() as mock:
        client = openai.OpenAI(base_url=mock.base_url(sc), api_key="t", max_retries=0)
        with pytest.raises(openai.NotFoundError):
            client.chat.completions.create(model="gpt-5-nano", messages=calls.PROMPT)


@pytest.mark.parametrize(
    ("sid", "tokens"),
    [
        ("gemini/thinking", (1609, 241 + 1877, 1877)),
        ("gemini/stream-thinking", (2083, 317 + 1999, 1999)),
    ],
)
def test_gemini_output_tokens_add_the_thoughts_to_the_candidates(sid, tokens):
    # candidatesTokenCount leaves out thoughtsTokenCount; totalTokenCount is their sum with input.
    t = scenarios.truth(scenarios.get(sid))
    assert (t.input_tokens, t.output_tokens, t.reasoning_tokens) == tokens
    usage = scenarios.get(sid).response
    last = usage.body if usage.body is not None else usage.events[-1].data
    meta = last["usageMetadata"]
    assert meta["totalTokenCount"] == t.input_tokens + t.output_tokens


@pytest.mark.parametrize(
    ("sid", "tokens"),
    [("gemini/cache-hit", (5417, 4099)), ("gemini/stream-cache-hit", (7229, 6151))],
)
def test_gemini_prompt_tokens_already_include_the_cached_tokens(sid, tokens):
    t = scenarios.truth(scenarios.get(sid))
    assert (t.input_tokens, t.cache_read_tokens) == tokens


def test_gemini_early_exit_grades_the_prompt_but_not_a_running_output_count():
    sc = scenarios.get("gemini/stream-early-exit")
    seen = sc.response.events[1].data["usageMetadata"]
    assert (seen["promptTokenCount"], seen["candidatesTokenCount"]) == (1949, 239)
    t = scenarios.truth(sc)
    assert (t.input_tokens, t.output_tokens, t.finish_reasons) == (1949, None, None)
    assert t.unread_counts == (359,)


def test_a_gemini_stream_carries_running_usage_on_every_chunk():
    # As recorded from the live API: the prompt on every chunk, candidates counted so far.
    chunks = scenarios.get("gemini/stream-thinking").response.events
    usage = [e.data["usageMetadata"] for e in chunks]
    assert [u["promptTokenCount"] for u in usage] == [2083] * 3
    assert [u["candidatesTokenCount"] for u in usage] == [105, 211, 317]
    assert [u["thoughtsTokenCount"] for u in usage] == [1999] * 3
    assert all(
        u["totalTokenCount"] == u["promptTokenCount"] + u["candidatesTokenCount"] + 1999
        for u in usage
    )


def test_a_gemini_response_names_the_requested_model():
    t = scenarios.truth(scenarios.get("gemini/basic"))
    assert t.response_model == t.request_model == "gemini-3.5-flash-lite"


@pytest.mark.parametrize("sid", ["gemini/stream-dropped", "gemini/async-stream-dropped"])
def test_gemini_dropped_stream_is_an_error(sid):
    assert scenarios.truth(scenarios.get(sid)).error == "connection dropped"


def test_gemini_has_no_with_block_variant():
    # The google-genai stream is a plain generator, not a context manager.
    assert "gemini/stream-early-exit-with" not in scenarios.SCENARIOS
    assert scenarios.get("gemini/async-stream-early-exit").asynchronous


@pytest.mark.parametrize("sid", ["azure/basic", "azure/async-basic"])
def test_an_azure_request_names_the_deployment_and_the_response_the_model(sid):
    t = scenarios.truth(scenarios.get(sid))
    assert t.request_model == "wt-chat-deployment"
    assert (t.response_model, t.response_id) == ("gpt-5-mini-2026-08-07", "chatcmpl-wt-azbasic")
    assert (t.input_tokens, t.output_tokens, t.finish_reasons) == (1213, 277, ("stop",))


def test_an_azure_stream_takes_its_id_and_model_after_the_prompt_filter_chunk():
    sc = scenarios.get("azure/stream")
    first = sc.response.events[0].data
    assert (first["id"], first["model"], first["choices"]) == ("", "", [])
    t = scenarios.truth(sc)
    assert (t.response_model, t.response_id) == ("gpt-5-mini-2026-08-07", "chatcmpl-wt-azstream")
    assert (t.input_tokens, t.output_tokens) == (2341, 433)


def test_only_the_azure_filter_fields_are_accepted_beyond_the_sdk_types():
    parsed = openai.types.chat.ChatCompletion.model_validate(
        scenarios.get("azure/basic").response.body
    )
    found = {re.sub(r"(^|\.)\d+\.", r"\1", p) for p in _undeclared(parsed)}
    assert found == az.AZURE_ONLY
