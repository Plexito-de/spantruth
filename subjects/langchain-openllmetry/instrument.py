"""The documented setup: OpenLLMetry's LangChain instrumentor with a caller-supplied provider.

The README shows `LangchainInstrumentor().instrument()`, which uses the global provider. Every
OpenTelemetry instrumentor also takes `tracer_provider=`, and the harness hands each subject its
provider, so it is passed here, as in the `openllmetry` subject. The spans are the same: the
global provider that the driver sets afterwards is this same provider.
"""


def instrument(tracer_provider, otlp_endpoint):
    from opentelemetry.instrumentation.langchain import LangchainInstrumentor

    LangchainInstrumentor().instrument(tracer_provider=tracer_provider)
