"""The reference instrumentation, as written."""

from spantruth import reference


def instrument(tracer_provider, otlp_endpoint, meter_provider=None):
    reference.instrument(tracer_provider, meter_provider=meter_provider)
