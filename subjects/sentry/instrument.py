"""The documented setup, sentry_sdk.init() with tracing on, and a local adapter to OpenTelemetry.

Sentry spans are not OpenTelemetry spans, and without a DSN the SDK has no transport, so nothing
leaves the process. The adapter opens one OpenTelemetry span when the SDK creates a `gen_ai.*` span
and ends it when the SDK finishes that span, so a span Sentry never finishes stays open and is
reported as lost. It copies the keys and values of the span's data unchanged, and maps the status.

Sentry SDK 3.0 removed `sentry_sdk.tracing` and its transaction-based spans. Every span is now a
streamed `sentry_sdk.traces.Span` that keeps its op in the `sentry.op` attribute and its data in
its attributes. The setup is the same in both major versions, so one adapter serves both: it picks
the span classes of the installed version.
"""

import json

SCOPE = "sentry_sdk.adapter"


def _attribute(value):
    # OpenTelemetry takes scalars and lists of scalars. Anything else keeps its content as JSON.
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)) and all(
        isinstance(v, (str, bool, int, float)) for v in value
    ):
        return list(value)
    return json.dumps(value, default=str)


def _shadow(tracer):
    """Return the start and end hooks that mirror the SDK's gen_ai spans as OpenTelemetry spans."""
    from opentelemetry.trace import Status, StatusCode

    open_spans = {}

    def start(span, op):
        if (op or "").startswith("gen_ai."):
            open_spans[id(span)] = tracer.start_span(op)

    def end(span, name, data, status):
        shadow = open_spans.pop(id(span), None)
        if shadow is None:
            return
        shadow.update_name(name)
        for key, value in data.items():
            if value is not None:
                shadow.set_attribute(key, _attribute(value))
        if status == "ok":
            shadow.set_status(Status(StatusCode.OK))
        elif status:
            shadow.set_status(Status(StatusCode.ERROR, status))
        shadow.end()

    return start, end


def _adapt_2(start, end):
    # Sentry SDK 2.x: Transaction.__init__ calls Span.__init__, and each class has its own finish.
    from sentry_sdk import tracing

    def init(original):
        def wrapper(self, *args, **kwargs):
            original(self, *args, **kwargs)
            start(self, self.op)

        return wrapper

    def finish(original):
        def wrapper(self, *args, **kwargs):
            name = self.description or getattr(self, "name", None) or self.op
            end(self, name, self._data, self.status)
            return original(self, *args, **kwargs)

        return wrapper

    tracing.Span.__init__ = init(tracing.Span.__init__)
    tracing.Span.finish = finish(tracing.Span.finish)
    tracing.Transaction.finish = finish(tracing.Transaction.finish)


def _adapt_3(start, end):
    # Sentry SDK 3.x: end(), finish() and __exit__ all go through Span._end. NoOpSpan, the span of
    # an unsampled trace, overrides both methods, so it is not mirrored.
    from sentry_sdk import traces

    def init(original):
        def wrapper(self, *args, **kwargs):
            original(self, *args, **kwargs)
            start(self, self.get_attributes().get("sentry.op"))

        return wrapper

    def finish(original):
        def wrapper(self, *args, **kwargs):
            # _end adds its last attributes, such as the conversation ID, so read them after it.
            result = original(self, *args, **kwargs)
            end(self, self.name, self.get_attributes(), self.status)
            return result

        return wrapper

    traces.Span.__init__ = init(traces.Span.__init__)
    traces.Span._end = finish(traces.Span._end)


def instrument(tracer_provider, otlp_endpoint):
    import sentry_sdk
    from opentelemetry import trace

    trace.set_tracer_provider(tracer_provider)
    tracer = tracer_provider.get_tracer(SCOPE, sentry_sdk.VERSION)
    start, end = _shadow(tracer)

    if int(sentry_sdk.VERSION.split(".")[0]) >= 3:
        _adapt_3(start, end)
    else:
        _adapt_2(start, end)

    sentry_sdk.init(traces_sample_rate=1.0)
