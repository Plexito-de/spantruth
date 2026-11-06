"""The documented setup: mlflow.openai.autolog() and mlflow.anthropic.autolog(), exporting OTLP.

MLflow exports OTLP/HTTP itself when OTEL_EXPORTER_OTLP_ENDPOINT is set, which the harness does.
`MLFLOW_USE_DEFAULT_TRACER_PROVIDER=false` joins the app's OpenTelemetry trace, and
`MLFLOW_ENABLE_OTEL_GENAI_SEMCONV=true` is MLflow's own option to export GenAI convention names.
"""


def instrument(tracer_provider, otlp_endpoint):
    from opentelemetry import trace

    # MLflow adds its OTLP processor to a global provider when one is set.
    trace.set_tracer_provider(tracer_provider)

    import mlflow

    # MLflow adds its processors when its first span starts (tracing/provider.py:707). Set them up
    # now, or the app's own parent span predates them and MLflow cannot start a child under it
    # (tracing/fluent.py:832, "Failed to start span").
    mlflow.tracing.enable()
    mlflow.openai.autolog()
    mlflow.anthropic.autolog()
