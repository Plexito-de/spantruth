"""The documented setup: OpenInference's LangChain instrumentor with a caller-supplied provider."""


def instrument(tracer_provider, otlp_endpoint):
    from openinference.instrumentation.langchain import LangChainInstrumentor

    LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
