"""Every verdict the checks can return, on hand-built records. No subprocess, no network."""

import json

import pytest

from spantruth import scenarios
from spantruth.checks import cell_verdict, grade
from spantruth.conventions import lookup
from spantruth.receiver import SpanRecord

TRACE, PARENT = "a" * 32, "b" * 16
BASE = {"exception": None, "seen": {"text": "x", "events": 0, "response_id": "r", "usage": None}}


def span(attrs, name="chat", trace=TRACE, status="UNSET", span_id="c" * 16):
    return SpanRecord(trace, span_id, PARENT, name, 3, 1, 2, status, "", attrs, {}, "lib", ())


def obs(probe=None, **seen):
    return {
        "exception": seen.pop("exception", None),
        "seen": BASE["seen"] | seen,
        "parent": {"trace_id": TRACE, "span_id": PARENT},
        "probe": probe or {},
    }


def by_id(checks):
    return {c.id: c for c in checks}


def honest(sid):
    t = scenarios.truth(scenarios.get(sid))
    attrs = {"gen_ai.operation.name": "chat", "gen_ai.request.model": t.request_model}
    for fact, name in [
        ("response_model", "gen_ai.response.model"),
        ("response_id", "gen_ai.response.id"),
        ("input_tokens", "gen_ai.usage.input_tokens"),
        ("output_tokens", "gen_ai.usage.output_tokens"),
        ("cache_read_tokens", "gen_ai.usage.cache_read.input_tokens"),
        ("cache_write_tokens", "gen_ai.usage.cache_creation.input_tokens"),
        ("reasoning_tokens", "gen_ai.usage.reasoning.output_tokens"),
    ]:
        if getattr(t, fact) is not None:
            attrs[name] = getattr(t, fact)
    if t.finish_reasons:
        attrs["gen_ai.response.finish_reasons"] = list(t.finish_reasons)
    return t, attrs


def test_correct_span_passes():
    sc = scenarios.get("anthropic/cache-hit")
    t, attrs = honest(sc.id)
    checks = grade(sc, t, BASE, obs(), [span(attrs)])
    assert cell_verdict(checks) == "pass", [c for c in checks if c.verdict != "pass"]


def test_anthropic_input_without_cache_is_wrong_and_the_formula_is_named():
    sc = scenarios.get("anthropic/cache-hit")
    t, attrs = honest(sc.id)
    attrs["gen_ai.usage.input_tokens"] = 1187
    c = by_id(grade(sc, t, BASE, obs(), [span(attrs)]))["input_tokens"]
    assert (c.verdict, c.expected, c.got) == ("wrong", 5802, 1187)
    assert c.detail == "cache reads and writes left out"


def test_cache_reads_counted_twice_is_named():
    sc = scenarios.get("anthropic/cache-hit")
    t, attrs = honest(sc.id)
    attrs["gen_ai.usage.input_tokens"] = 5802 + 4096 + 519
    assert by_id(grade(sc, t, BASE, obs(), [span(attrs)]))["input_tokens"].detail == (
        "cache tokens counted twice"
    )


def test_reasoning_tokens_left_out_of_output_is_named():
    sc = scenarios.get("anthropic/thinking")
    t, attrs = honest(sc.id)
    attrs["gen_ai.usage.output_tokens"] = t.output_tokens - t.reasoning_tokens
    assert by_id(grade(sc, t, BASE, obs(), [span(attrs)]))["output_tokens"].detail == (
        "reasoning tokens left out of output"
    )


@pytest.mark.parametrize(
    ("fact", "name"),
    [
        ("cache_read_tokens", "gen_ai.usage.input_tokens.cached"),
        ("cache_write_tokens", "gen_ai.usage.input_tokens.cache_write"),
        ("cache_write_tokens", "gen_ai.usage.cache_write_input_tokens"),
        ("reasoning_tokens", "gen_ai.usage.output_tokens.reasoning"),
        ("reasoning_tokens", "gen_ai.usage.reasoning_output_tokens"),
    ],
)
def test_a_vendor_name_for_a_count_is_read(fact, name):
    assert lookup({name: 5}, fact) == (name, 5)


def test_absent_value_is_missing_not_wrong():
    sc = scenarios.get("openai/basic")
    t, attrs = honest(sc.id)
    del attrs["gen_ai.response.id"]
    assert by_id(grade(sc, t, BASE, obs(), [span(attrs)]))["response_id"].verdict == "missing"


def test_openinference_names_are_read_and_reported():
    sc = scenarios.get("openai/basic")
    t = scenarios.truth(sc)
    attrs = {
        "llm.token_count.prompt": 1187,
        "llm.token_count.completion": 263,
        "llm.model_name": t.response_model,
    }
    c = by_id(grade(sc, t, BASE, obs(), [span(attrs)]))
    assert (
        c["input_tokens"].verdict == "pass" and c["input_tokens"].where == "llm.token_count.prompt"
    )


def test_langfuse_names_are_read_and_reported():
    sc = scenarios.get("openai/basic")
    t = scenarios.truth(sc)
    attrs = {
        "langfuse.observation.type": "generation",
        "langfuse.observation.model.name": t.response_model,
        "langfuse.observation.usage_details": json.dumps(
            {"prompt_tokens": 1187, "completion_tokens": 263}
        ),
    }
    c = by_id(grade(sc, t, BASE, obs(), [span(attrs)]))
    assert (
        c["input_tokens"].verdict == "pass"
        and c["input_tokens"].where == "langfuse.observation.usage_details[prompt_tokens]"
    )
    assert (
        c["output_tokens"].verdict == "pass"
        and c["output_tokens"].where == "langfuse.observation.usage_details[completion_tokens]"
    )
    assert c["response_model"].verdict == "pass"
    assert c["request_model"].verdict == "n/a"
    assert c["response_id"].verdict == "n/a"
    assert c["finish_reasons"].verdict == "n/a"
    assert c["cache_write_tokens"].verdict == "n/a"


def test_langfuse_observation_model_name_accepts_request_model():
    sc = scenarios.get("openai/basic")
    t = scenarios.truth(sc)
    attrs = {
        "langfuse.observation.type": "generation",
        "langfuse.observation.model.name": t.request_model,
    }
    c = by_id(grade(sc, t, BASE, obs(), [span(attrs)]))
    assert c["response_model"].verdict == "pass"


def test_span_that_never_ended_is_lost():
    sc = scenarios.get("openai/stream-early-exit")
    t = scenarios.truth(sc)
    probe = {"d" * 16: {"name": "chat gpt-5-mini", "start_ns": 1, "end_ns": None}}
    checks = grade(sc, t, BASE, obs(probe), [])
    assert cell_verdict(checks) == "lost"


def test_no_span_at_all_is_missing():
    sc = scenarios.get("openai/basic")
    checks = grade(sc, scenarios.truth(sc), BASE, obs(), [])
    assert by_id(checks)["span.recorded"].verdict == "missing"


def test_changed_exception_is_broken():
    sc = scenarios.get("openai/stream-dropped")
    base = BASE | {"exception": "httpx.RemoteProtocolError"}
    checks = grade(sc, scenarios.truth(sc), base, obs(), [])
    assert by_id(checks)["app.unchanged"].verdict == "broken"


def test_hang_is_broken():
    sc = scenarios.get("openai/basic")
    assert cell_verdict(grade(sc, scenarios.truth(sc), BASE, {"hang": True}, [])) == "broken"


def test_error_recorded_as_success_is_wrong():
    sc = scenarios.get("openai/rate-limited")
    attrs = {"gen_ai.request.model": "gpt-5-mini"}
    c = by_id(grade(sc, scenarios.truth(sc), BASE, obs(), [span(attrs, status="UNSET")]))
    assert c["error.status"].verdict == "wrong" and c["error.type"].verdict == "missing"


def test_error_status_on_successful_operation_is_wrong():
    sc = scenarios.get("openai/retry-429")
    t, attrs = honest(sc.id)
    c = by_id(grade(sc, t, BASE, obs(), [span(attrs, status="ERROR")]))
    assert c["status.unset"].verdict == "wrong"
    assert c["status.unset"].expected == "UNSET"
    assert c["status.unset"].got == "ERROR"


def test_any_error_type_passes():
    sc = scenarios.get("anthropic/rate-limited")
    attrs = {"gen_ai.request.model": "m", "error.type": "RateLimitError"}
    c = by_id(grade(sc, scenarios.truth(sc), BASE, obs(), [span(attrs, status="ERROR")]))
    assert c["error.status"].verdict == "pass" and c["error.type"].verdict == "pass"


def test_span_in_another_trace_is_wrong():
    sc = scenarios.get("openai/basic")
    t, attrs = honest(sc.id)
    c = by_id(grade(sc, t, BASE, obs(), [span(attrs, trace="f" * 32)]))
    assert c["span.parented"].verdict == "wrong"


def test_usage_on_two_spans_is_a_double_count():
    sc = scenarios.get("openai/stream")
    t, attrs = honest(sc.id)
    two = [span(attrs), span(attrs, name="streaming response", span_id="e" * 16)]
    assert by_id(grade(sc, t, BASE, obs(), two))["tokens.single-report"].verdict == "wrong"


def test_count_from_the_unread_stream_is_wrong():
    sc = scenarios.get("openai/stream-early-exit")
    t = scenarios.truth(sc)
    attrs = {"gen_ai.request.model": "gpt-5-mini", "gen_ai.usage.output_tokens": 347}
    c = by_id(grade(sc, t, BASE, obs(), [span(attrs)]))["output_tokens"]
    assert c.verdict == "wrong" and "unread" in c.detail


def test_estimated_tokens_on_a_stream_without_usage_are_wrong():
    sc = scenarios.get("openai/stream-no-usage")
    attrs = {"gen_ai.request.model": "gpt-5-mini", "gen_ai.usage.output_tokens": 15}
    c = by_id(grade(sc, scenarios.truth(sc), BASE, obs(), [span(attrs)]))["output_tokens"]
    assert c.verdict == "wrong" and "estimated" in c.detail


def test_zero_for_an_absent_count_is_fine():
    sc = scenarios.get("openai/stream-no-usage")
    attrs = {"gen_ai.request.model": "gpt-5-mini", "gen_ai.usage.output_tokens": 0}
    c = by_id(grade(sc, scenarios.truth(sc), BASE, obs(), [span(attrs)]))["output_tokens"]
    assert c.verdict == "n/a"


def test_normalized_finish_reason_is_accepted():
    sc = scenarios.get("anthropic/basic")
    t, attrs = honest(sc.id)
    attrs["gen_ai.response.finish_reasons"] = ["stop"]
    assert by_id(grade(sc, t, BASE, obs(), [span(attrs)]))["finish_reasons"].verdict == "pass"


def test_instrument_failure_is_inconclusive_not_a_verdict_on_the_library():
    sc = scenarios.get("openai/basic")
    checks = grade(sc, scenarios.truth(sc), BASE, {"instrument_error": "ImportError"}, [])
    assert cell_verdict(checks) == "inconclusive"


def test_fact_outside_openinference_conventions_is_not_graded():
    sc = scenarios.get("openai/basic")
    t = scenarios.truth(sc)
    attrs = {"llm.token_count.prompt": 1187, "llm.model_name": t.response_model}
    c = by_id(grade(sc, t, BASE, obs(), [span(attrs)]))
    assert c["response_id"].verdict == "n/a"
    assert c["finish_reasons"].verdict == "missing"


def test_fact_outside_langfuse_conventions_is_not_graded():
    sc = scenarios.get("openai/basic")
    t = scenarios.truth(sc)
    attrs = {
        "langfuse.observation.type": "generation",
        "langfuse.observation.model.name": t.response_model,
    }
    c = by_id(grade(sc, t, BASE, obs(), [span(attrs)]))
    assert c["response_id"].verdict == "n/a"
    assert c["request_model"].verdict == "n/a"
    assert c["finish_reasons"].verdict == "n/a"
    assert c["cache_write_tokens"].verdict == "n/a"


def test_lookup_follows_dotted_json_key():
    usage = {
        "prompt_tokens_details": {"cached_tokens": 4096},
        "completion_tokens_details": {"reasoning_tokens": 1792},
    }
    attrs = {"langfuse.observation.usage_details": json.dumps(usage)}
    assert lookup(attrs, "cache_read_tokens") == (
        "langfuse.observation.usage_details[prompt_tokens_details.cached_tokens]",
        4096,
    )
    assert lookup(attrs, "reasoning_tokens") == (
        "langfuse.observation.usage_details[completion_tokens_details.reasoning_tokens]",
        1792,
    )


def test_with_block_variant_exists():
    sc = scenarios.get("anthropic/stream-early-exit-with")
    assert sc.with_block and sc.read_events == 2


def test_error_type_is_not_graded_on_openinference_spans():
    sc = scenarios.get("openai/rate-limited")
    attrs = {"llm.system": "openai", "llm.invocation_parameters": "{}"}
    c = by_id(grade(sc, scenarios.truth(sc), BASE, obs(), [span(attrs, status="ERROR")]))
    assert c["error.status"].verdict == "pass" and c["error.type"].verdict == "n/a"


def test_error_type_is_not_graded_on_langfuse_spans():
    sc = scenarios.get("openai/rate-limited")
    attrs = {
        "langfuse.observation.type": "generation",
        "langfuse.observation.model.name": "gpt-5-mini",
    }
    c = by_id(grade(sc, scenarios.truth(sc), BASE, obs(), [span(attrs, status="ERROR")]))
    assert c["error.status"].verdict == "pass" and c["error.type"].verdict == "n/a"


def test_finish_reasons_as_a_json_string_are_wrong_and_say_so():
    sc = scenarios.get("openai/basic")
    t, attrs = honest(sc.id)
    attrs["gen_ai.response.finish_reasons"] = '["stop"]'
    c = by_id(grade(sc, t, BASE, obs(), [span(attrs)]))["finish_reasons"]
    assert c.verdict == "wrong"
    assert "JSON string" in c.detail and "string array" in c.detail
