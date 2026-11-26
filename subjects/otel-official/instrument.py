"""The documented setup: each instrumentor with a caller-supplied tracer provider."""


def instrument(tracer_provider, otlp_endpoint):
    from opentelemetry.instrumentation.genai.anthropic import AnthropicInstrumentor
    from opentelemetry.instrumentation.genai.openai import OpenAIInstrumentor
    from opentelemetry.instrumentation.google_genai import GoogleGenAiSdkInstrumentor

    OpenAIInstrumentor().instrument(tracer_provider=tracer_provider)
    AnthropicInstrumentor().instrument(tracer_provider=tracer_provider)
    GoogleGenAiSdkInstrumentor().instrument(tracer_provider=tracer_provider)
