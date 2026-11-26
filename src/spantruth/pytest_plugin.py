"""A pytest plugin: grade the spans of your own LLM wrapper in your own test suite.

    def test_a_stream_gets_its_tokens(spantruth):
        spantruth.run("openai/stream").assert_pass()

The fake provider and an OTLP receiver run inside the test process. The spans that arrive are
graded by `checks.grade`, the code that grades a cell of `spantruth run`, so a check means the
same thing in both places. pytest loads this module through the `pytest11` entry point when
spantruth is installed. It needs the OpenTelemetry SDK and OTLP exporter: `spantruth[pytest]`.

One check is not graded here. `app.unchanged` compares an instrumented run with an uninstrumented
one in another process, and a test has only the instrumented one. It is `n/a`.
"""

from __future__ import annotations

import gc
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

from . import calls, checks, scenarios, subjects
from .checks import Check
from .mockserver import MockServer, base_url
from .model import Scenario
from .receiver import Receiver, SpanRecord

NOT_COMPARED = "not graded in pytest: it needs an uninstrumented run in another process"


def scenario_ids(provider: str | None = None, api: str | None = None) -> list[str]:
    """The scenario IDs, for `pytest.mark.parametrize`. Filter by provider and by OpenAI API."""
    return [
        sid
        for sid, sc in scenarios.SCENARIOS.items()
        if provider in (None, sc.provider) and api in (None, sc.api)
    ]


@dataclass
class Result:
    """The graded spans of one call. `checks` are the same records a results document holds."""

    scenario: Scenario
    checks: list[Check]
    spans: list[SpanRecord]
    exception: str | None = None

    @property
    def verdict(self) -> str:
        return checks.cell_verdict(self.checks)

    @property
    def failing(self) -> dict[str, str]:
        """Check ID to verdict for every check that did not pass, as in an expectation file."""
        return {c.id: c.verdict for c in self.checks if c.verdict in checks.FAILING}

    def explain(self) -> str:
        lines = [f"{self.scenario.id}: {self.verdict}"]
        for c in self.checks:
            if c.verdict not in checks.FAILING:
                continue
            line = f"  {c.id}: {c.verdict}"
            if c.expected is not None or c.got is not None:
                line += f", expected {c.expected!r}, got {c.got!r}"
            if c.where:
                line += f" in {c.where}"
            if c.detail:
                line += f" ({c.detail})"
            lines.append(line)
            if rule := c.as_dict()["rule_url"]:
                lines.append(f"    rule: {rule}")
        names = ", ".join(s.name for s in self.spans) or "none"
        lines.append(f"  spans received: {names}")
        return "\n".join(lines)

    def assert_pass(self) -> None:
        """Fail the test with every failing check, its expected and reported value, and its rule."""
        if self.failing:
            raise AssertionError(self.explain())


@dataclass
class Call:
    """One scenario call in progress. Point your client at `base_url`. The block sets `result`."""

    scenario: Scenario
    base_url: str
    result: Result | None = field(default=None, init=False)


@contextmanager
def _probe() -> Iterator[dict[str, dict[str, Any]]]:
    """The driver's span probe for the length of one call, then the SDK as it was."""
    from opentelemetry.sdk import trace as sdk_trace

    from .driver import _install_probe

    original = sdk_trace.Span.start, sdk_trace.Span.end
    try:
        yield _install_probe()
    finally:
        sdk_trace.Span.start, sdk_trace.Span.end = original


class Spantruth:
    """The fake provider, the receiver and the tracer provider for one test session."""

    def __init__(self, mock: MockServer, receiver: Receiver, tracer_provider: Any) -> None:
        self.mock, self.receiver, self.tracer_provider = mock, receiver, tracer_provider

    def base_url(self, scenario_id: str) -> str:
        """The `base_url` an SDK client needs to get this scenario's response."""
        return base_url(self.mock.url, scenarios.get(scenario_id))

    @contextmanager
    def call(self, scenario_id: str) -> Iterator[Call]:
        """Make the call yourself inside the block, the way your application does.

        The block is the app's own span, so a span of your wrapper belongs inside its trace. When
        the block ends, the spans that arrived are graded and put in `call.result`. Catch the
        exception of an error scenario inside the block, as your application would.
        """
        sc = scenarios.get(scenario_id)
        self.mock.reset()  # a retry scenario starts again from its first response
        current = Call(sc, self.base_url(scenario_id))
        first = len(self.receiver.spans)
        tracer = self.tracer_provider.get_tracer("spantruth.pytest")
        with _probe() as probe:
            with tracer.start_as_current_span("spantruth.scenario") as parent:
                yield current
            if any(rec["end_ns"] is None for rec in probe.values()):
                gc.collect()  # a stream the app dropped ends its span now, as in the driver
            self.tracer_provider.force_flush()
        ctx = parent.get_span_context()
        obs = {
            "exception": None,
            "seen": {},
            "parent": {
                "trace_id": format(ctx.trace_id, "032x"),
                "span_id": format(ctx.span_id, "016x"),
            },
            "probe": {sid: dict(rec) for sid, rec in probe.items()},
        }
        spans = list(self.receiver.spans[first:])
        graded = [
            Check("app.unchanged", "n/a", detail=NOT_COMPARED) if c.id == "app.unchanged" else c
            for c in checks.grade(sc, scenarios.truth(sc), obs, obs, spans)
        ]
        current.result = Result(sc, graded, spans)

    def run(self, scenario_id: str) -> Result:
        """Make the scenario's call the way `spantruth run` does, with the SDK, and grade it.

        Your wrapper must already be attached, for example with `monkeypatch`. The exception that
        an error scenario raises is kept in `Result.exception`, not raised.
        """
        exception = None
        with self.call(scenario_id) as current:
            try:
                calls.run(current.scenario, current.base_url, calls.Seen())
            except Exception as exc:  # an error scenario raises on purpose
                exception = f"{type(exc).__module__.split('.')[0]}.{type(exc).__name__}"
        assert current.result is not None
        current.result.exception = exception
        return current.result


def _tracer_provider(otlp: str) -> tuple[Any, Any]:
    """The global SDK tracer provider, exporting to the receiver. Set one when there is none.

    A wrapper that calls `trace.get_tracer()` then reports to the receiver with no change. A
    provider that the test suite set already keeps its own processors and gets one more. That one
    stops exporting when the session ends, because the receiver is gone then.
    """
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    class SessionProcessor(SimpleSpanProcessor):
        active = True

        def on_end(self, span) -> None:
            if self.active:
                super().on_end(span)

    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider(resource=Resource.create({"service.name": "spantruth"}))
        trace.set_tracer_provider(provider)
        if trace.get_tracer_provider() is not provider:
            raise pytest.UsageError("spantruth: the global tracer provider is not an SDK provider")
    processor = SessionProcessor(OTLPSpanExporter(f"{otlp}/v1/traces"))
    provider.add_span_processor(processor)
    return provider, processor


@pytest.fixture(scope="session")
def spantruth_mock() -> Iterator[MockServer]:
    """The fake provider on 127.0.0.1. It serves every scenario, chosen by the URL path."""
    with MockServer() as mock:
        yield mock


@pytest.fixture(scope="session")
def spantruth_receiver() -> Iterator[Receiver]:
    """An OTLP/HTTP receiver on 127.0.0.1 that keeps every span in memory."""
    with Receiver() as receiver:
        yield receiver


@pytest.fixture(scope="session")
def _spantruth_session(spantruth_mock, spantruth_receiver) -> Iterator[Spantruth]:
    # The variables every subject of `spantruth run` gets, such as the Anthropic SDK's own tracing
    # switched off, so your code is graded on its own spans. A value that you set wins.
    added = {k: v for k, v in subjects.base().get("env", {}).items() if k not in os.environ}
    os.environ.update(added)
    provider, processor = _tracer_provider(spantruth_receiver.url)
    try:
        yield Spantruth(spantruth_mock, spantruth_receiver, provider)
    finally:
        processor.active = False
        for key in added:
            os.environ.pop(key, None)


@pytest.fixture
def spantruth(_spantruth_session) -> Spantruth:
    """Run a scenario against the fake provider and grade the spans your code produced."""
    return _spantruth_session
