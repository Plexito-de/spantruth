"""The documented setup: `litellm.callbacks = ["otel"]`.

LiteLLM exports to OTEL_EXPORTER_OTLP_ENDPOINT, which the harness sets, and uses the global tracer
provider when one is already set. The driver sets its own provider as the global one.
"""


def instrument(tracer_provider, otlp_endpoint):
    import litellm

    litellm.callbacks = ["otel"]
