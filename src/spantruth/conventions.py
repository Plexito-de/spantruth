"""Where each fact lives, under every naming convention the suite knows.

A check reads a fact under any of these names, in this order, so a library is never marked
wrong for its choice of names. The first name of each fact is the current OpenTelemetry GenAI
name at the pinned semantic-conventions-genai commit.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass
from typing import Any

# open-telemetry/semantic-conventions-genai main, 2026-10-09. The repo has no releases yet.
SEMCONV_GENAI_COMMIT = "6fd0d763"


@dataclass(frozen=True)
class Fact:
    current: str
    others: tuple[str, ...] = ()
    # (attribute, key) pairs for conventions that store the fact inside a JSON string.
    json: tuple[tuple[str, str], ...] = ()
    # False when the OpenInference conventions define no attribute for this fact.
    in_openinference: bool = True
    # False when the Langfuse conventions define no attribute for this fact.
    in_langfuse: bool = True

    @property
    def names(self) -> tuple[str, ...]:
        return (self.current, *self.others)


FACTS: dict[str, Fact] = {
    "provider": Fact(
        "gen_ai.provider.name",
        ("gen_ai.system", "llm.provider", "llm.system"),
        in_langfuse=False,
    ),
    "request_model": Fact(
        "gen_ai.request.model",
        ("llm.request.model_name",),
        json=(("llm.invocation_parameters", "model"),),
        in_langfuse=False,
    ),
    "response_model": Fact(
        "gen_ai.response.model",
        ("llm.response.model_name", "llm.model_name", "langfuse.observation.model.name"),
    ),
    "response_id": Fact("gen_ai.response.id", in_openinference=False, in_langfuse=False),
    "finish_reasons": Fact(
        "gen_ai.response.finish_reasons", ("llm.finish_reason",), in_langfuse=False
    ),
    "input_tokens": Fact(
        "gen_ai.usage.input_tokens",
        ("gen_ai.usage.prompt_tokens", "llm.usage.prompt_tokens", "llm.token_count.prompt"),
        json=(("langfuse.observation.usage_details", "prompt_tokens"),),
    ),
    "output_tokens": Fact(
        "gen_ai.usage.output_tokens",
        (
            "gen_ai.usage.completion_tokens",
            "llm.usage.completion_tokens",
            "llm.token_count.completion",
        ),
        json=(("langfuse.observation.usage_details", "completion_tokens"),),
    ),
    "cache_read_tokens": Fact(
        "gen_ai.usage.cache_read.input_tokens",
        (
            "gen_ai.usage.cache_read_input_tokens",
            "gen_ai.usage.input_tokens.cached",  # Sentry
            "llm.token_count.prompt_details.cache_read",
        ),
        json=(("langfuse.observation.usage_details", "prompt_tokens_details.cached_tokens"),),
    ),
    "cache_write_tokens": Fact(
        "gen_ai.usage.cache_write.input_tokens",
        (
            "gen_ai.usage.cache_creation.input_tokens",
            "gen_ai.usage.cache_creation_input_tokens",
            "gen_ai.usage.input_tokens.cache_write",  # Sentry
            "gen_ai.usage.cache_write_input_tokens",  # ddtrace
            "llm.token_count.prompt_details.cache_write",
        ),
        in_langfuse=False,
    ),
    "reasoning_tokens": Fact(
        "gen_ai.usage.reasoning.output_tokens",
        (
            "gen_ai.usage.reasoning_tokens",
            "gen_ai.usage.output_tokens.reasoning",  # Sentry
            "gen_ai.usage.reasoning_output_tokens",  # ddtrace
            "llm.token_count.completion_details.reasoning",
        ),
        json=(
            ("langfuse.observation.usage_details", "completion_tokens_details.reasoning_tokens"),
        ),
    ),
}

# Facts that identify a span as an LLM call. The driver's own span carries none of them.
LLM_MARKERS = (
    frozenset(name for fact in FACTS.values() for name in fact.names)
    | {"gen_ai.operation.name", "langfuse.observation.type"}
    | {a for fact in FACTS.values() for a, _ in fact.json}
)

# Provider finish reasons and the normalized values some libraries record instead.
FINISH_ALIASES = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_call",
    "tool_calls": "tool_call",
    "max_tokens": "length",
    # Gemini's FinishReason enum
    "STOP": "stop",
    "MAX_TOKENS": "length",
}


def openinference_only(attributes: dict[str, Any]) -> bool:
    keys = attributes.keys()
    return any(k.startswith("llm.") for k in keys) and not any(
        k.startswith("gen_ai.") for k in keys
    )


def langfuse_only(attributes: dict[str, Any]) -> bool:
    keys = attributes.keys()
    return any(k.startswith("langfuse.") for k in keys) and not any(
        k.startswith("gen_ai.") for k in keys
    )


def defined_for(attributes: dict[str, Any], fact: str) -> bool:
    """False when the span follows only OpenInference or Langfuse and has no name for `fact`."""
    if openinference_only(attributes):
        return FACTS[fact].in_openinference
    if langfuse_only(attributes):
        return FACTS[fact].in_langfuse
    return True


def lookup(attributes: dict[str, Any], fact: str) -> tuple[str, Any] | None:
    """Return (attribute name, value) for the first name of `fact` present, or None."""
    spec = FACTS[fact]
    for name in spec.names:
        if name in attributes and attributes[name] not in (None, ""):
            return name, attributes[name]
    for name, key in spec.json:
        raw = attributes.get(name)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except ValueError:
                continue
            for part in key.split("."):
                parsed = parsed.get(part) if isinstance(parsed, dict) else None
            if parsed is not None:
                return f"{name}[{key}]", parsed
    return None


def same_finish(expected: tuple[str, ...], got: Any) -> bool:
    if isinstance(got, str):
        got = [got]
    if not isinstance(got, (list, tuple)):
        return False

    def norm(values) -> list[str]:
        return sorted(FINISH_ALIASES.get(v, v) for v in values)

    return norm(expected) == norm(got)


# Where a library records the completion text. Each convention stores the text in a message
# structure, often as a JSON string, so `text_of` reads the text out of any of these shapes.
OUTPUT_ATTRIBUTES = (
    "gen_ai.output.messages",  # current GenAI conventions, on the span or on the details event
    "gen_ai.completion",  # older GenAI event attribute
    "gen_ai.response.text",  # Sentry
    "langfuse.observation.output",
)
# Conventions that flatten one message per index into attribute names.
OUTPUT_INDEXED = (
    re.compile(r"gen_ai\.completion\.(\d+)\.content"),  # OpenLLMetry legacy attributes
    re.compile(r"llm\.output_messages\.(\d+)\.message\.content"),  # OpenInference
    # OpenInference, a message of content blocks
    re.compile(r"llm\.output_messages\.(\d+)\.message\.contents\.(\d+)\.message_content\.text"),
)
# Log records and span events that carry the completion in their body.
OUTPUT_EVENTS = ("gen_ai.choice",)


def text_of(value: Any) -> str | None:
    """The text inside a message, a list of messages or parts, or a JSON string of either."""
    if isinstance(value, str):
        if value[:1] in ("[", "{"):
            try:
                return text_of(json.loads(value))
            except ValueError:
                return value
        return value
    if isinstance(value, list):
        texts = [t for t in (text_of(v) for v in value) if t is not None]
        return "".join(texts) if texts else None
    if isinstance(value, dict):
        if isinstance(value.get("parts"), list):
            parts = value["parts"]
            return text_of([p for p in parts if isinstance(p, dict) and p.get("type") == "text"])
        for key in ("content", "text", "message"):
            if key in value:
                return text_of(value[key])
    return None


def output_texts(attributes: dict[str, Any]) -> list[tuple[str, str]]:
    """(attribute, text) for every completion text recorded in one set of attributes."""
    out = []
    for name in OUTPUT_ATTRIBUTES:
        if name in attributes and (text := text_of(attributes[name])) is not None:
            out.append((name, text))
    for pattern in OUTPUT_INDEXED:
        indexed = sorted(
            (tuple(map(int, m.groups())), n) for n in attributes if (m := pattern.fullmatch(n))
        )
        if indexed:
            text = "".join(text_of(attributes[n]) or "" for _, n in indexed)
            out.append((indexed[0][1], text))
    return out


def leaves(value: Any):
    """Every string inside a value, with JSON strings opened, so a search sees nested text."""
    if isinstance(value, str):
        yield value
        if value[:1] in ("[", "{"):
            with contextlib.suppress(ValueError):
                yield from leaves(json.loads(value))
    elif isinstance(value, dict):
        for v in value.values():
            yield from leaves(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from leaves(v)


# The token usage metrics, per token direction: (metric, the `gen_ai.token.type` it needs). The
# pinned conventions split usage into per-direction histograms and counters. The single
# `gen_ai.client.token.usage` histogram with `gen_ai.token.type` is their deprecated predecessor,
# which many libraries still emit, so it is read too.
TOKEN_METRICS: dict[str, tuple[tuple[str, str | None], ...]] = {
    "input_tokens": (
        ("gen_ai.client.inference.operation.input_tokens", None),
        ("gen_ai.client.inference.usage.input_tokens", None),
        ("gen_ai.client.token.usage", "input"),
    ),
    "output_tokens": (
        ("gen_ai.client.inference.operation.output_tokens", None),
        ("gen_ai.client.inference.usage.output_tokens", None),
        ("gen_ai.client.token.usage", "output"),
    ),
}
TOKEN_METRIC_NAMES = frozenset(name for names in TOKEN_METRICS.values() for name, _ in names)
