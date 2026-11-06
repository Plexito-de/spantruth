"""The documented setup: each instrumentor with a caller-supplied tracer provider."""


def instrument(tracer_provider, otlp_endpoint):
    from opentelemetry.instrumentation.genai.anthropic import AnthropicInstrumentor

    AnthropicInstrumentor().instrument(tracer_provider=tracer_provider)
