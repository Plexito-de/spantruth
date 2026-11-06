"""One sync Messages call, traced. SDK tracing is controlled only by ANTHROPIC_OPEN_TELEMETRY in the environment.
Usage: call.py <lib: none|oi|ll|otel> <project> <otlp_url> <stub_url> <spans_jsonl> <mode> [--parent]
mode: plain (messages.create), stream-c (messages.create(stream=True)), stream-h (messages.stream).
--parent wraps the call in an app span that carries no token counts.
The spans_jsonl tee records all attributes of each exported span, so backend numbers can be checked against input."""
import json, os, sys
lib, project, otlp, stub, spans_path, mode = sys.argv[1:7]
parent = "--parent" in sys.argv[7:]
model = "claude-sonnet-4-5-20250929"
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

class Tee(SpanExporter):
    def export(self, spans):
        with open(spans_path, "a") as f:
            for s in spans:
                f.write(json.dumps({
                    "project": project, "name": s.name, "scope": s.instrumentation_scope.name,
                    "trace_id": f"{s.context.trace_id:032x}", "span_id": f"{s.context.span_id:016x}",
                    "parent": f"{s.parent.span_id:016x}" if s.parent else None,
                    "status": s.status.status_code.name,
                    "attributes": {k: (list(v) if isinstance(v, tuple) else v) for k, v in dict(s.attributes or {}).items()},
                }) + "\n")
        return SpanExportResult.SUCCESS
    def shutdown(self): pass

provider = TracerProvider(resource=Resource({"service.name": project, "openinference.project.name": project, "deployment.environment": project.lower()}))
provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(endpoint=otlp)))
provider.add_span_processor(SimpleSpanProcessor(Tee()))
trace.set_tracer_provider(provider)

if lib == "oi":
    from openinference.instrumentation.anthropic import AnthropicInstrumentor as I
elif lib == "ll":
    from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor as I
elif lib == "otel":
    from opentelemetry.instrumentation.genai.anthropic import AnthropicInstrumentor as I
if lib != "none":
    I().instrument(tracer_provider=provider)

import anthropic
client = anthropic.Anthropic(api_key="stub-not-a-key", base_url=stub, max_retries=0)
args = dict(model=model, max_tokens=256, messages=[{"role": "user", "content": "ping"}])

def one_call():
    if mode == "plain":
        return client.messages.create(**args).usage.model_dump()
    if mode == "stream-c":
        seen = {}
        for ev in client.messages.create(stream=True, **args):
            if ev.type == "message_start":
                seen = ev.message.usage.model_dump()
            elif ev.type == "message_delta":
                seen["output_tokens"] = ev.usage.output_tokens
        return seen
    if mode == "stream-h":
        with client.messages.stream(**args) as s:
            for _ in s.text_stream:
                pass
            return s.get_final_message().usage.model_dump()
    raise SystemExit(f"unknown mode {mode}")

if parent:
    with trace.get_tracer("app").start_as_current_span("app.handle_request"):
        u = one_call()
else:
    u = one_call()
print(project, "client saw:", {k: u.get(k) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}, "ANTHROPIC_OPEN_TELEMETRY=", os.environ.get("ANTHROPIC_OPEN_TELEMETRY"))
provider.force_flush(); provider.shutdown()
