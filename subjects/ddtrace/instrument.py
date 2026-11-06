"""The documented setup, LLMObs.enable(), and a local adapter to OpenTelemetry.

ddtrace spans are not OpenTelemetry spans. The adapter opens one OpenTelemetry span when ddtrace
starts an LLM span and ends it when ddtrace finishes that span, so a span ddtrace never finishes
stays open and is reported as lost. It copies the keys and values of the span's tags unchanged.
No Agent and no account: `agentless_enabled=False` sends LLMObs events to a local Agent, which is
absent, and they are dropped.
"""

SCOPE = "ddtrace.adapter"


def instrument(tracer_provider, otlp_endpoint):
    from ddtrace import __version__
    from ddtrace.internal import core
    from ddtrace.llmobs import LLMObs
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode

    trace.set_tracer_provider(tracer_provider)
    tracer = tracer_provider.get_tracer(SCOPE, __version__)
    open_spans = {}

    def on_start(span):
        if span.span_type == "llm":
            open_spans[span.span_id] = tracer.start_span(span.name)

    def on_finish(span):
        shadow = open_spans.pop(span.span_id, None)
        if shadow is None:
            return
        shadow.update_name(span.name)
        for key, value in {**span.get_tags(), **span.get_metrics()}.items():
            shadow.set_attribute(key, value)
        if span.error:
            shadow.set_status(Status(StatusCode.ERROR, span.get_tag("error.message") or ""))
        shadow.end()

    LLMObs.enable(ml_app="spantruth", agentless_enabled=False, integrations_enabled=True)

    # After LLMObs.enable(), so these listeners run after the listeners that write the final tags.
    core.on("trace.span_start", on_start)
    core.on("trace.span_finish", on_finish)
