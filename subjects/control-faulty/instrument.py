"""The reference instrumentation with every known fault switched on."""

from spantruth import reference

FAULTS = {
    "anthropic-raw-input",
    "close-leaks-span",
    "content-first-chunk",
    "content-ignores-off",
    "errors-as-success",
    "estimate-tokens",
    "gemini-candidates-output",
    "metric-reasoning-left-out",
    "retry-as-error",
}


def instrument(tracer_provider, otlp_endpoint, meter_provider=None):
    reference.instrument(tracer_provider, faults=FAULTS, meter_provider=meter_provider)
