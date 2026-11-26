"""The mock provider and the OTLP receiver, driven by the real SDKs and the real exporter."""

import anthropic
import openai
import pytest
from google import genai
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.trace import StatusCode

from spantruth import scenarios
from spantruth.mockserver import MockServer
from spantruth.receiver import Receiver

ALL = list(scenarios.SCENARIOS.values())


@pytest.fixture(scope="module")
def mock():
    with MockServer() as server:
        yield server


def _consume(sc, mock):
    """Run the scenario through the real SDK. Return (events or response, exception)."""
    if sc.provider == "gemini":
        client = genai.Client(
            api_key="test", http_options=genai.types.HttpOptions(base_url=mock.base_url(sc))
        )
        kw = {"model": sc.request_model, "contents": "hi"}
        call = (
            client.models.generate_content_stream if sc.stream else client.models.generate_content
        )
        try:
            result = call(**kw)
            return (list(result) if sc.stream else result), None
        except Exception as exc:
            return None, exc
    if sc.api == "responses":
        client = openai.OpenAI(base_url=mock.base_url(sc), api_key="test", max_retries=sc.retries)
        kw = {"model": sc.request_model, "input": "hi", "stream": sc.stream}
        call = client.responses.create
    elif sc.provider in ("openai", "azure"):
        client = (
            openai.AzureOpenAI(
                azure_endpoint=mock.base_url(sc),
                api_version="2024-10-21",
                api_key="test",
                max_retries=sc.retries,
            )
            if sc.provider == "azure"
            else openai.OpenAI(base_url=mock.base_url(sc), api_key="test", max_retries=sc.retries)
        )
        kw = {"model": sc.request_model, "messages": [{"role": "user", "content": "hi"}]}
        if sc.stream:
            kw["stream"] = True
        if sc.stream and sc.stream_usage:
            kw["stream_options"] = {"include_usage": True}
        call = client.chat.completions.create
    else:
        client = anthropic.Anthropic(
            base_url=mock.base_url(sc), api_key="test", max_retries=sc.retries
        )
        kw = {
            "model": sc.request_model,
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": sc.stream,
        }
        call = client.messages.create
    try:
        result = call(**kw)
        return (list(result) if sc.stream else result), None
    except Exception as exc:  # the scenario decides which exception is right
        return None, exc


@pytest.mark.parametrize("sc", [s for s in ALL if not s.asynchronous], ids=lambda s: s.id)
def test_every_fixture_round_trips_through_the_real_sdk(sc, mock):
    result, exc = _consume(sc, mock)
    if sc.response.status == 429:
        if sc.provider == "gemini":
            assert type(exc).__name__ == "ClientError" and exc.status == "RESOURCE_EXHAUSTED"
        else:
            assert type(exc).__name__ == "RateLimitError"
    elif sc.expects_error:
        assert exc is not None, "a dropped connection or error event must raise"
    elif sc.stream:
        assert exc is None and len(result) == sum(
            isinstance(e.data, dict) for e in sc.response.events
        )
    elif sc.provider == "gemini":
        assert exc is None and result.response_id == sc.response.body["responseId"]
    else:
        assert exc is None and result.id == sc.response.body["id"]


def test_openai_stream_usage_reaches_the_app(mock):
    sc = scenarios.get("openai/stream-cache-hit")
    chunks, _ = _consume(sc, mock)
    usage = chunks[-1].usage
    assert (usage.prompt_tokens, usage.prompt_tokens_details.cached_tokens) == (7213, 6144)


def test_unknown_scenario_is_a_404(mock):
    client = openai.OpenAI(base_url=f"{mock.url}/s/nope/v1", api_key="t", max_retries=0)
    with pytest.raises(openai.NotFoundError):
        client.chat.completions.create(model="m", messages=[])


def test_receiver_decodes_otlp_protobuf_from_the_real_exporter():
    with Receiver() as rx:
        tp = TracerProvider(resource=Resource.create({"service.name": "t"}))
        tp.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(f"{rx.url}/v1/traces")))
        tracer = tp.get_tracer("test")
        with (
            tracer.start_as_current_span("parent") as parent,
            tracer.start_as_current_span("chat m") as child,
        ):
            child.set_attribute("gen_ai.usage.input_tokens", 1187)
            child.set_attribute("gen_ai.response.finish_reasons", ["stop"])
            child.set_status(StatusCode.ERROR, "boom")
        tp.shutdown()
        spans = {s.name: s for s in rx.spans}
    assert set(spans) == {"parent", "chat m"}
    chat = spans["chat m"]
    assert chat.attributes["gen_ai.usage.input_tokens"] == 1187
    assert chat.attributes["gen_ai.response.finish_reasons"] == ["stop"]
    assert chat.status == "ERROR"
    assert chat.parent_span_id == spans["parent"].span_id
    assert chat.trace_id == format(parent.get_span_context().trace_id, "032x")
    assert chat.resource["service.name"] == "t"


def test_receiver_keeps_log_records_and_span_event_attributes():
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor

    with Receiver() as rx:
        lp = LoggerProvider()
        lp.add_log_record_processor(SimpleLogRecordProcessor(OTLPLogExporter(f"{rx.url}/v1/logs")))
        lp.get_logger("lib").emit(
            event_name="gen_ai.choice",
            body={"message": {"content": "Paris"}},
            attributes={"gen_ai.provider.name": "openai"},
        )
        lp.shutdown()
        tp = TracerProvider()
        tp.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(f"{rx.url}/v1/traces")))
        with tp.get_tracer("t").start_as_current_span("chat m") as s:
            s.add_event("gen_ai.content.completion", {"gen_ai.completion": "Paris"})
        tp.shutdown()
        [record], [chat] = rx.logs, rx.spans
    assert (record.event_name, record.body) == ("gen_ai.choice", {"message": {"content": "Paris"}})
    assert record.attributes == {"gen_ai.provider.name": "openai"}
    assert rx.counts["logs"] == 1
    assert chat.events == ("gen_ai.content.completion",)
    assert chat.event_attributes == ({"gen_ai.completion": "Paris"},)


def test_receiver_drops_other_paths():
    import urllib.request

    with Receiver() as rx:
        req = urllib.request.Request(
            f"{rx.url}/api/public/otel/v1/traces", data=b"\x00", method="POST"
        )
        assert urllib.request.urlopen(req).status == 200
        assert rx.spans == [] and rx.errors == []


def test_mock_server_retries_429_per_scenario(mock):
    sc = scenarios.get("openai/retry-429")
    mock.reset()

    # With retries enabled, SDK retries 429 and succeeds on the second attempt
    client = openai.OpenAI(base_url=mock.base_url(sc), api_key="test", max_retries=2)
    resp = client.chat.completions.create(
        model=sc.request_model, messages=[{"role": "user", "content": "hi"}]
    )
    assert resp.id == "chatcmpl-wt-retry"
    assert len([r for r in mock.requests if r[0] == sc.id]) == 2

    # With retries disabled, the 429 raises RateLimitError
    client_no_retry = openai.OpenAI(base_url=mock.base_url(sc), api_key="test", max_retries=0)
    with pytest.raises(openai.RateLimitError):
        client_no_retry.chat.completions.create(
            model=sc.request_model, messages=[{"role": "user", "content": "hi"}]
        )


def test_mock_server_retries_without_retry_header(mock):
    sc = scenarios.get("openai/retry-429")
    mock.reset()
    r1 = mock.next_response(sc)
    r2 = mock.next_response(sc)
    assert r1.status == 429
    assert r2.status == 200


def test_gemini_requests_reach_the_methods_of_the_real_api(mock):
    mock.reset()
    for sid in ("gemini/basic", "gemini/stream"):
        _consume(scenarios.get(sid), mock)
    paths = [path for _sid, path, _body in mock.requests]
    assert paths[0].endswith("/models/gemini-3.5-flash-lite:generateContent")
    assert paths[1].endswith("/models/gemini-3.5-flash-lite:streamGenerateContent?alt=sse")


def test_a_gemini_request_to_the_wrong_method_is_a_404(mock):
    # A stream scenario asked for a single response: the fake provider refuses it, so a fixture
    # cannot answer a call the real API would route elsewhere.
    sc = scenarios.get("gemini/stream")
    client = genai.Client(
        api_key="test", http_options=genai.types.HttpOptions(base_url=mock.base_url(sc))
    )
    with pytest.raises(genai.errors.ClientError) as caught:
        client.models.generate_content(model=sc.request_model, contents="hi")
    assert caught.value.code == 404


def test_azure_requests_reach_the_deployment_with_an_api_version(mock):
    mock.reset()
    _consume(scenarios.get("azure/basic"), mock)
    ((_sid, path, _body),) = mock.requests
    assert path.endswith(
        "/openai/deployments/wt-chat-deployment/chat/completions?api-version=2024-10-21"
    )


def test_an_azure_request_without_the_deployment_path_is_a_404(mock):
    sc = scenarios.get("azure/basic")
    client = openai.OpenAI(base_url=f"{mock.base_url(sc)}/openai", api_key="test", max_retries=0)
    with pytest.raises(openai.NotFoundError):
        client.chat.completions.create(model=sc.request_model, messages=[])
