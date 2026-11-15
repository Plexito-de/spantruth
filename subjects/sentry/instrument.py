"""The documented setup, sentry_sdk.init() with tracing on, and a local adapter to OpenTelemetry.

Sentry spans are not OpenTelemetry spans, and without a DSN the SDK has no transport, so nothing
leaves the process. The adapter opens one OpenTelemetry span when the SDK creates a `gen_ai.*` span
and ends it when the SDK finishes that span, so a span Sentry never finishes stays open and is
reported as lost. It copies the keys and values of the span's data unchanged, and maps the status.
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


def instrument(tracer_provider, otlp_endpoint):
    import sentry_sdk
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
    from sentry_sdk import tracing

    trace.set_tracer_provider(tracer_provider)
    tracer = tracer_provider.get_tracer(SCOPE, sentry_sdk.VERSION)
    open_spans = {}

    def init(original):
        def wrapper(self, *args, **kwargs):
            original(self, *args, **kwargs)
            if (self.op or "").startswith("gen_ai."):
                open_spans[id(self)] = tracer.start_span(self.op)

        return wrapper

    def finish(original):
        def wrapper(self, *args, **kwargs):
            shadow = open_spans.pop(id(self), None)
            if shadow is not None:
                shadow.update_name(self.description or getattr(self, "name", None) or self.op)
                for key, value in self._data.items():
                    if value is not None:
                        shadow.set_attribute(key, _attribute(value))
                if self.status == "ok":
                    shadow.set_status(Status(StatusCode.OK))
                elif self.status:
                    shadow.set_status(Status(StatusCode.ERROR, self.status))
                shadow.end()
            return original(self, *args, **kwargs)

        return wrapper

    # Transaction.__init__ calls Span.__init__, and each class has its own finish.
    tracing.Span.__init__ = init(tracing.Span.__init__)
    tracing.Span.finish = finish(tracing.Span.finish)
    tracing.Transaction.finish = finish(tracing.Transaction.finish)

    sentry_sdk.init(traces_sample_rate=1.0)
