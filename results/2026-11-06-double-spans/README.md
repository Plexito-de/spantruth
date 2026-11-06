# Double spans on two backends, 2026-11-06

What two LLM observability backends report for one Anthropic call, when the SDK's own tracing and an
instrumentation library both write a span.

Each configuration made one sync, non-streaming `messages.create` for `claude-sonnet-4-5-20250929` against a local stub that returned 1000
input tokens and 200 output tokens. The stub logged one HTTP request per run, so no call was sent twice. All
numbers come from each backend's API. They are in `phoenix-r1.json`, `phoenix-r2.json` and `opik-o1.json`.

| Name in the files | Meaning |
| --- | --- |
| `A-` | SDK tracing on, the default |
| `B-` | `ANTHROPIC_OPEN_TELEMETRY=false` |
| `C-` | SDK tracing alone, with no library |
| `oi` | openinference-instrumentation-anthropic 3.3.0, anthropic 2.4.0 |
| `oi310` | openinference-instrumentation-anthropic 3.1.0, anthropic 2.4.0 |
| `ll` | opentelemetry-instrumentation-anthropic (OpenLLMetry) 0.62.4, anthropic 2.4.0 |
| `otel` | opentelemetry-instrumentation-genai-anthropic 1.3b0, anthropic 2.4.0 |
| `*latest` | openinference 3.3.1, OpenLLMetry 0.63.0, GenAI 1.3b0, with anthropic 2.6.0 |

Backends: Arize Phoenix 21.0.0 and Opik 2.2.143, both self-hosted, with telemetry off. `scripts/` holds what
ran. Each `run_*.sh` expects one virtual environment per library (`venv-oi`, `venv-ll` and so on), the stub on
port 18099, and the backend on the port the script names.

Each call has a global tracer provider and no parent span in the app. Both backends price the model at $3 and
$15 per million tokens, which matches [Anthropic's list price](https://platform.claude.com/docs/en/about-claude/pricing)
for Claude Sonnet 4.5 on 2026-11-06.

Not measured: real API calls, streaming, async calls, retries, the backends' web UI, each library's quickstart,
and any other backend.
