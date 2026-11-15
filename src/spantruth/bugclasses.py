"""The ten ways LLM telemetry goes wrong, with names a person can say and cite.

A class is a reading of a check, not a new check. Results documents and expectation files name
checks, and those IDs never change meaning, so a document of any age gets classes when it renders.
A class ID follows the same policy as a check ID: it never changes meaning, and a new meaning gets
a new ID.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Instance:
    """A public report of the bug: a thread that anyone can read, and who reported it."""

    label: str
    url: str
    credit: str


@dataclass(frozen=True)
class BugClass:
    id: str
    name: str
    summary: str
    dashboard: str  # What a person sees on a dashboard because of this bug.
    instances: tuple[Instance, ...] = ()
    remedy: str = ""  # What an application can do today, when that does not wait for a release.


def _i(label: str, url: str, credit: str) -> Instance:
    return Instance(label, f"https://github.com/{url}", credit)


CLASSES = (
    BugClass(
        "ST01",
        "Ghost span",
        "The app made the call, and no finished span ever reaches a backend.",
        "The call is not there. Its cost, its latency and its error are all absent, and nothing "
        "says so. Streams that the app leaves early or that fail mid-way are the usual victims.",
        (
            _i(
                "openllmetry#4536",
                "traceloop/openllmetry/issues/4536",
                "armanunix",
            ),
            _i(
                "openllmetry#4643",
                "traceloop/openllmetry/issues/4643",
                "chrikrah",
            ),
            _i(
                "openinference#3790, fixed",
                "Arize-ai/openinference/issues/3790",
                "feiiiiii5",
            ),
            _i(
                "openinference#3930, fixed",
                "Arize-ai/openinference/issues/3930",
                "armanunix",
            ),
            _i("openlit#1828", "openlit/openlit/issues/1828", "chrikrah"),
        ),
    ),
    BugClass(
        "ST02",
        "Double span",
        "One call reports its token usage on two spans.",
        "A backend that sums token attributes across spans counts the call twice. The Anthropic "
        "SDK traces itself since 1.14.0, so a library on top causes this unless it handles the "
        "SDK's span, as OpenInference does since openinference-instrumentation-anthropic 3.1.1.",
        (
            _i(
                "langfuse#18978: dashboards show about 2x",
                "langfuse/langfuse/issues/18978",
                "rvndbalaji",
            ),
            _i(
                "opentelemetry-python-genai#971",
                "open-telemetry/opentelemetry-python-genai/issues/971",
                "lmolkova",
            ),
            _i(
                "openinference#4119, fixed",
                "Arize-ai/openinference/issues/4119",
                "PabloPardoGarcia",
            ),
        ),
        "Let one side report the call: set ANTHROPIC_OPEN_TELEMETRY=false, or pass "
        "open_telemetry=False to the client.",
    ),
    BugClass(
        "ST03",
        "Cache-blind tokens",
        "Cached tokens are left out of the input count, or their own counts are wrong.",
        "The cost of a cached call is wrong, because a cache read costs a fraction of an input "
        "token and a cache write costs more than one. Your cache hit rate is wrong too.",
        (_i("openllmetry#4583", "traceloop/openllmetry/issues/4583", "armanunix"),),
    ),
    BugClass(
        "ST04",
        "Phantom tokens",
        "The span reports more tokens than the provider sent, or invents a count.",
        "The cost is too high. When a stream carries no usage and the library estimates from the "
        "text, the number looks real and is not.",
        (_i("openllmetry#4377", "traceloop/openllmetry/pull/4377", "arthi-arumugam-git"),),
    ),
    BugClass(
        "ST05",
        "Vanishing tokens",
        "The span reports fewer tokens than the provider sent, or none.",
        "The cost is too low, and a token budget or a rate alert fires late or never.",
        (
            _i("sentry-python#7948", "getsentry/sentry-python/issues/7948", "chrikrah"),
            _i("logfire#2549", "pydantic/logfire/issues/2549", "chrikrah"),
        ),
    ),
    BugClass(
        "ST06",
        "Hidden reasoning",
        "The reasoning tokens that the provider reported do not appear on the span.",
        "Reasoning is billed as output, so the total can be right while the reason for it is "
        "gone: you cannot see how much of your bill is thinking.",
        (
            _i("openllmetry#4660", "traceloop/openllmetry/issues/4660", "chrikrah"),
            _i("inspect_ai#5815", "UKGovernmentBEIS/inspect_ai/issues/5815", "chrikrah"),
        ),
    ),
    BugClass(
        "ST07",
        "False OK",
        "A call failed, and its span does not say so, or does not say how.",
        "Your error rate is too low. A stream that dropped its connection counts as a success in "
        "every panel that filters on status or on `error.type`.",
        (
            _i("openllmetry#4535", "traceloop/openllmetry/pull/4535", "feiiiiii5"),
            _i("langfuse-python#2039", "langfuse/langfuse-python/issues/2039", "chrikrah"),
        ),
    ),
    BugClass(
        "ST08",
        "False alarm",
        "A call succeeded, and its span says it failed.",
        "Your error rate is too high. A 429 that the SDK retried and recovered from pages "
        "somebody for an outage that the user never saw.",
    ),
    BugClass(
        "ST09",
        "Silent stop",
        "The finish reason is missing or wrong.",
        "You cannot tell a complete answer from one that hit `max_tokens` or a content filter, "
        "so truncated answers look the same as finished ones.",
        (
            _i(
                "anthropic-sdk-python#2055",
                "anthropics/anthropic-sdk-python/issues/2055",
                "chrikrah",
            ),
        ),
    ),
    BugClass(
        "ST10",
        "Observer effect",
        "The instrumentation changes what the application receives.",
        "The telemetry is not the worst part: your app behaves differently with tracing on. It "
        "gets other chunks, another exception, or it hangs.",
    ),
)

BY_ID = {c.id: c for c in CLASSES}

# The classes whose findings move a cost figure. The others leave the token counts alone, and a
# figure from their cell would be another class's.
COST_CLASSES = frozenset({"ST01", "ST02", "ST03", "ST04", "ST05"})

# The class of each check. Input and output counts depend on the direction of the error.
BY_CHECK = {
    "span.ended": "ST01",
    "span.recorded": "ST01",
    "tokens.single-report": "ST02",
    "cache_read_tokens": "ST03",
    "cache_write_tokens": "ST03",
    "input_tokens": "ST05",
    "output_tokens": "ST05",
    "reasoning_tokens": "ST06",
    "error.status": "ST07",
    "error.type": "ST07",
    "status.unset": "ST08",
    "finish_reasons": "ST09",
    "app.unchanged": "ST10",
}

# Checks that are not one of the ten. They still show as findings, under their check ID.
UNCLASSIFIED = frozenset(
    {
        "harness",
        "request_model",
        "response_id",
        "response_model",
        "span.parented",
        "subject.instrument",
    }
)


def classify(finding: Mapping[str, Any]) -> BugClass | None:
    """The class of one failing check, or None when the check is not one of the ten."""
    check = finding["id"]
    if check not in BY_CHECK:
        return None
    if check in ("input_tokens", "output_tokens") and finding.get("verdict") == "wrong":
        expected, got = finding.get("expected"), finding.get("got")
        if "cache" in (finding.get("detail") or ""):
            return BY_ID["ST03"]
        if expected is None or (
            isinstance(got, (int, float)) and isinstance(expected, (int, float)) and got > expected
        ):
            return BY_ID["ST04"]
    return BY_ID[BY_CHECK[check]]
