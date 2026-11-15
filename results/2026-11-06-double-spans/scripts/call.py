"""One Messages call, traced. Usage: call.py <lib: none|oi|ll|otel> <project> <otlp_url> <stub_url> <spans_jsonl> [model]
SDK tracing is controlled only by ANTHROPIC_OPEN_TELEMETRY in the environment."""
import json, os, sys
lib, project, otlp, stub, spans_path = sys.argv[1:6]
model = sys.argv[6] if len(sys.argv) > 6 else "claude-sonnet-4-5-20250929"
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

class Tee(SpanExporter):
    """Local record of exactly what was exported, so backend numbers can be checked against input."""
    def export(self, spans):
        with open(spans_path, "a") as f:
            for s in spans:
                a = dict(s.attributes or {})
                f.write(json.dumps({
                    "project": project, "name": s.name, "scope": s.instrumentation_scope.name,
                    "trace_id": f"{s.context.trace_id:032x}", "span_id": f"{s.context.span_id:016x}",
                    "parent": f"{s.parent.span_id:016x}" if s.parent else None,
                    "usage": {k: v for k, v in a.items() if "token" in k or "usage" in k or k.endswith("model") or "model_name" in k or "kind" in k.lower() or k in ("llm.provider","llm.system","gen_ai.system","gen_ai.provider.name","gen_ai.operation.name")},
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
r = client.messages.create(model=model, max_tokens=256, messages=[{"role": "user", "content": "ping"}])
print(project, "response usage:", r.usage.input_tokens, r.usage.output_tokens, "ANTHROPIC_OPEN_TELEMETRY=", os.environ.get("ANTHROPIC_OPEN_TELEMETRY"))
provider.force_flush(); provider.shutdown()
