"""The documented setup for an OpenTelemetry backend other than Logfire: set the global tracer
provider, then `Agent.instrument_all()`. Every agent then traces with the default settings
(semantic convention version 5).
"""


def instrument(tracer_provider, otlp_endpoint):
    from opentelemetry import trace
    from pydantic_ai import Agent

    trace.set_tracer_provider(tracer_provider)
    Agent.instrument_all()
