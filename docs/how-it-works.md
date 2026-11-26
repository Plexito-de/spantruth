# How spantruth works

spantruth replays provider responses through LLM instrumentation libraries and checks the values
they report against what the provider actually sent: tokens, cache tokens, models, finish reasons,
errors, and whether the span ends at all.

```text
spantruth run
  ├─ fake provider endpoint     serves the scenario: JSON, SSE stream, 429, dropped connection
  ├─ OTLP/HTTP receiver         collects every span the library exports
  └─ per library: own venv  →  the app calls the real OpenAI / Anthropic SDK
                                    └─ the library under test exports OTLP to the receiver
  checks: wire truth  ×  what the app saw  ×  what the telemetry says  →  verdict per cell
```

- **Values, not presence.** No two token counts in a fixture are equal. A swapped field,
  a dropped cache count, or a sum done twice shows up as a wrong number. For a wrong token count,
  the report names the arithmetic that most likely produced it, such as "cache reads and writes
  left out".
- **Fixtures follow the wire.** The test suite parses every response and every stream event with
  the provider SDK's own types.
- **Convention-neutral.** A value is read under the current OpenTelemetry GenAI names, older names,
  or OpenInference names. A library is never marked wrong for its choice of names.
- **The app is checked too.** Each scenario also runs without instrumentation. If the library
  changes what the app receives (an exception, a chunk, a return value, a hang), the cell is
  `broken`.
- **Spans that never end are found.** An in-process probe records every span start and end. A span
  that never ends is never exported, so the receiver alone could not tell.
- **Offline and deterministic.** No API keys. The first run downloads each library once, and after
  that it needs no network.

## The controls

Two subjects run the suite's own small reference instrumentation. `control-honest` has to pass
every check. `control-faulty` carries four planted faults and has to fail exactly the checks
they target. CI runs both end to end on every change. If either moves, the suite is wrong, not a
library.

## Verdicts

| Verdict | Meaning |
| --- | --- |
| pass | Every graded value matches the wire. |
| missing | The span ended without a value that the wire carried. |
| wrong | A reported value does not match the wire. |
| lost | The span never ended, so no backend ever receives it. |
| broken | The app saw something different from an uninstrumented run. |
| inconclusive | The cell could not be graded: the harness could not run it, or the library's setup raised (`subject.instrument`). |

## The provider SDK's own spans

The Anthropic SDK traces itself since 1.14.0, when a global tracer provider is registered. spantruth
turns the SDK's spans off for the other library subjects (`ANTHROPIC_OPEN_TELEMETRY=false`), so a
library is graded on its own spans. The `anthropic-sdk` subject turns them back on. `doctor` leaves
them on, as your application does.

## Coverage

- **Providers:** OpenAI Chat Completions and Anthropic Messages, sync and async. The OpenAI
  Responses API has its first scenario, a stream replayed from a recording.
- **Scenarios (45):** plain call, stream, stream without usage, prompt-cache hit (plain, streamed,
  with cumulative counts in `message_delta`, with a 1-hour cache write next to a 5-minute one, and
  an OpenAI cache write on GPT-5.6), parallel tool calls, reasoning tokens, HTTP 429,
  a stream the app leaves early (by `close()` and by a `with` block), a dropped connection, and
  Anthropic's mid-stream `error` event. Streams and failures run sync and async. A Responses API
  stream cut after eight recorded events replays kartsan03/wiretruth's
  `stream.missing-finish-abort` fixture, vendored unchanged with its provenance.
  `uvx spantruth list` prints every scenario ID.
- **Subjects:** the libraries that the [README](../README.md#coverage) lists, plus the two controls.
  Sentry and ddtrace do not export OpenTelemetry spans, so a small local adapter copies their span data
  unchanged, and each subject's notes say what the adapter decides.
