"""The documented setup: `litellm.callbacks = ["otel"]`.

LiteLLM exports to OTEL_EXPORTER_OTLP_ENDPOINT, which the harness sets, and uses the global tracer
provider when one is already set. The driver sets its own provider as the global one.

LiteLLM writes its spans after the call returns: from a thread pool for a sync call, and from a
queue on the event loop for an async call, which its exit handler drains. A process that keeps
running gets those spans. So the flush first waits for both, as LiteLLM's exit handler does, and a
span is graded on what LiteLLM writes, not on how soon.
"""


class _DrainLiteLLM:
    def force_flush(self, timeout_millis=None):
        from litellm.litellm_core_utils.logging_worker import GLOBAL_LOGGING_WORKER
        from litellm.litellm_core_utils.thread_pool_executor import executor
        from opentelemetry import trace

        GLOBAL_LOGGING_WORKER._flush_on_exit()
        executor.shutdown(wait=True)
        return trace.get_tracer_provider().force_flush()


def instrument(tracer_provider, otlp_endpoint):
    import litellm

    litellm.callbacks = ["otel"]
    return [_DrainLiteLLM()]
