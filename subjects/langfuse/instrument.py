"""The documented setup: from langfuse.openai import openai, pointed at the receiver.

Langfuse requires keys to enable tracing, so pass a throwaway key to the constructor.
base_url points at the receiver so Langfuse's own export does not attempt an outbound connection.
"""


def instrument(tracer_provider, otlp_endpoint):
    import langfuse.openai  # noqa: F401
    from langfuse import Langfuse
    from opentelemetry import trace

    trace.set_tracer_provider(tracer_provider)
    Langfuse(
        public_key="pk-lf-test",
        secret_key="sk-lf-test",
        base_url=otlp_endpoint,
        tracer_provider=tracer_provider,
    )
