"""The reference instrumentation of the controls, on inputs that only some scenarios send."""

from openai.types.chat import ChatCompletionMessage
from opentelemetry.sdk.trace import TracerProvider

from spantruth import reference


def test_a_prompt_with_the_sdk_message_object_is_recorded():
    # An agent loop sends the assistant's reply back as the SDK's own object, not as a dict. With
    # content capture on, the reference reads it like a dict message instead of raising.
    tracer = TracerProvider().get_tracer("test")
    reply = ChatCompletionMessage(role="assistant", content="It is sunny.")
    prompt = [{"role": "user", "content": "Weather?"}, reply]
    call = reference._Call(tracer, "openai", "m", {"content-ignores-off"}, prompt)
    recorded = call.span.attributes["gen_ai.input.messages"]
    assert "Weather?" in recorded and "It is sunny." in recorded
