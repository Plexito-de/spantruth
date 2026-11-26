"""The documented setup: OpenLLMetry's LangChain instrumentor with a caller-supplied provider.

The README shows `LangchainInstrumentor().instrument()`, which uses the global provider. The
provider is passed here instead, as in the `openllmetry` subject, because the driver sets the
global provider only after `instrument()`.
"""


def instrument(tracer_provider, otlp_endpoint):
    from opentelemetry.instrumentation.langchain import LangchainInstrumentor

    LangchainInstrumentor().instrument(tracer_provider=tracer_provider)
