"""Attach the wrapper the way the application does. spantruth then drives the OpenAI SDK itself."""


def instrument(tracer_provider, otlp_endpoint):
    from acme_llm import traced
    from openai.resources.chat.completions import Completions
    from opentelemetry import trace

    trace.set_tracer_provider(tracer_provider)
    Completions.create = traced(Completions.create)
