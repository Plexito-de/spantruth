"""An in-house LLM wrapper, of the kind many companies write. It has two bugs on purpose.

It puts a span around each chat completion and records the model and the token counts. It looks
correct, and its tests pass, because they only cover a plain call that succeeds.
"""

from functools import wraps

from opentelemetry import trace


def traced(create):
    """Wrap `Completions.create` so that every call gets a span."""

    @wraps(create)
    def wrapper(self, *args, **kwargs):
        tracer = trace.get_tracer("acme_llm")
        with tracer.start_as_current_span(f"chat {kwargs.get('model')}") as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.provider.name", "openai")
            span.set_attribute("gen_ai.request.model", kwargs.get("model", ""))
            response = create(self, *args, **kwargs)
            # Bug 1: a stream has no `usage` yet, so a streamed call records no tokens at all.
            usage = getattr(response, "usage", None)
            if usage is not None:
                span.set_attribute("gen_ai.response.model", response.model)
                span.set_attribute("gen_ai.response.id", response.id)
                # Bug 2: OpenAI's `prompt_tokens` already includes cached tokens.
                details = usage.prompt_tokens_details
                cached = details.cached_tokens if details else 0
                span.set_attribute("gen_ai.usage.input_tokens", usage.prompt_tokens + cached)
                span.set_attribute("gen_ai.usage.output_tokens", usage.completion_tokens)
                span.set_attribute(
                    "gen_ai.response.finish_reasons",
                    [choice.finish_reason for choice in response.choices],
                )
            return response

    return wrapper
