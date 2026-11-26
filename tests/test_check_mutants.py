"""Tests for the lines that scripts/mutate.py found untested: each kills one surviving mutant.

The mutant each test kills is named in its docstring. scripts/mutate.py fails when a mutant
survives, so a test removed from here shows up in the weekly mutation run.
"""

from spantruth import scenarios
from spantruth.checks import grade
from spantruth.conventions import lookup, same_finish
from spantruth.receiver import SpanRecord

TRACE, PARENT = "a" * 32, "b" * 16
BASE = {"exception": None, "seen": {"text": "", "events": 0, "response_id": None, "usage": None}}
OBS = BASE | {"parent": {"trace_id": TRACE, "span_id": PARENT}, "probe": {}}


def span(attributes: dict) -> SpanRecord:
    return SpanRecord(
        TRACE, "c" * 16, PARENT, "chat", 3, 1, 2, "UNSET", "", attributes, {}, "x", ()
    )


def checks_for(sid: str, attributes: dict) -> dict:
    sc = scenarios.get(sid)
    return {c.id: c for c in grade(sc, scenarios.truth(sc), BASE, OBS, [span(attributes)])}


def test_input_counted_twice_is_named():
    """formula-doubled: `2 * total` in the guesses for a wrong input count."""
    t = scenarios.truth(scenarios.get("anthropic/cache-hit"))
    attributes = {"gen_ai.operation.name": "chat", "gen_ai.usage.input_tokens": 2 * t.input_tokens}
    c = checks_for("anthropic/cache-hit", attributes)["input_tokens"]
    assert (c.verdict, c.detail) == ("wrong", "counted twice")


def test_openinference_reasoning_tokens_are_read():
    """drop-openinference-reasoning: OpenInference's name for reasoning tokens."""
    name = "llm.token_count.completion_details.reasoning"
    assert lookup({name: 1792}, "reasoning_tokens") == (name, 1792)


def test_a_span_with_only_an_operation_name_is_an_llm_span():
    """drop-operation-marker: `gen_ai.operation.name` alone marks an LLM span."""
    found = checks_for("openai/basic", {"gen_ai.operation.name": "chat"})
    assert "span.recorded" not in found
    assert found["input_tokens"].verdict == "missing"


def test_max_tokens_is_the_same_finish_as_length():
    """drop-max-tokens-alias: Anthropic's `max_tokens` normalized to `length`."""
    assert same_finish(("max_tokens",), ["length"])


def test_tool_calls_is_the_same_finish_as_tool_call():
    """drop-tool-calls-alias: OpenAI's `tool_calls` normalized to `tool_call`."""
    assert same_finish(("tool_calls",), ["tool_call"])


def test_finish_reasons_compare_in_any_order():
    """finish-order-matters: two choices may finish in either order."""
    assert same_finish(("stop", "tool_calls"), ["tool_call", "stop"])
    assert not same_finish(("stop", "tool_calls"), ["stop", "stop"])
