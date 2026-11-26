"""The documented OpenTelemetry route for the OpenAI Agents SDK: OpenInference's instrumentor.

`OpenAIAgentsInstrumentor` registers a tracing processor with the Agents SDK. By default it is the
only one (`exclusive_processor=True`), so it replaces the SDK's own exporter to the OpenAI backend.
"""


def instrument(tracer_provider, otlp_endpoint):
    from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

    OpenAIAgentsInstrumentor().instrument(tracer_provider=tracer_provider)
