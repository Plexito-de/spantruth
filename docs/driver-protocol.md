# Driver Protocol

A `spantruth` driver runs scenarios inside an isolated subject environment (Python or Node.js), records what the application observed, and reports span lifecycle data back to the harness.

## Subcommands

Both `spantruth.driver` (Python) and `drivers/node/driver.mjs` (Node.js) expose the same CLI:

- **`baseline <scenario...> --mock <url> --out <file> [--plan <json-or-file>]`**  
  Runs one or more scenarios with no instrumentation attached and writes a JSON map of `{scenario_id: observation}` to `--out`.
- **`instrumented <scenario> --mock <url> --otlp <url> --subject <dir> --out <file> [--plan <json-or-file>]`**  
  Loads the subject's `instrument.py` or `instrument.mjs`, registers an OTLP/HTTP protobuf exporter pointing at `<otlp>/v1/traces`, installs the lost-span probe, wraps execution in a parent span (`spantruth.scenario`), flushes all tracer providers, and writes the single observation to `--out`.

## Call Plan Data

Scenarios are delivered as plain JSON call plans (`Scenario.call_plan()`), with `drivers/node/scenarios.json` generated from `src/spantruth/scenarios.py` as the Node driver's bundled copy:

```json
{
  "id": "openai/basic",
  "provider": "openai",
  "request_model": "gpt-5-mini",
  "stream": false,
  "stream_usage": true,
  "tools": false,
  "read_events": null,
  "with_block": false,
  "asynchronous": false,
  "api": "chat",
  "entrypoint": "create",
  "retries": 0
}
```

## Observation Output

Each observation JSON object has the following shape:

```json
{
  "scenario": "openai/basic",
  "hang": false,
  "exception": null,
  "seen": {
    "text": "The capital of France is Paris.",
    "events": 0,
    "response_id": "chatcmpl-wt-basic",
    "usage": { "prompt_tokens": 1187, "completion_tokens": 263, "total_tokens": 1450 }
  },
  "start_ns": 1700000000000000000,
  "end_ns": 1700000000045000000,
  "parent": {
    "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
    "span_id": "00f067aa0ba902b7"
  },
  "probe": {
    "00f067aa0ba902b7": {
      "name": "spantruth.scenario",
      "start_ns": 1700000000000000000,
      "end_ns": 1700000000045000000
    }
  }
}
```

- **Lost-span probe:** Records `start_ns` and `end_ns` for every SDK span created in the process. A span that never ended keeps `end_ns: null`.
- **Watchdog:** If a call hangs for 20 seconds, the driver writes `{"scenario": id, "hang": true}` and exits with code `3`.

