# A stream that drops: what the library reports, and what the span says

Draft for kartsan03/wiretruth#82, 2026-11-24. Not published. It goes to Tymur first; the wiretruth parts are his to check.

wiretruth and spantruth look at the same failure from two layers. wiretruth replays a recorded response through an
LLM library and grades what the library hands back to the caller. spantruth replays a response through a provider
SDK with an instrumentation library attached and grades the span that library writes. Each section below describes
its own layer only, as agreed in kartsan03/wiretruth#82: wiretruth takes no position on what a span should record.

## The three responses

All three are wiretruth fixtures at tag `v0.4.0`, in `suite/fixtures/<api>/`. Each is cut from the same API's recorded
text stream by a named transform:

| Fixture | What the response does | Transform |
|---|---|---|
| `stream.missing-finish` | the stream ends cleanly, without its finish signal | `strip_finish` |
| `stream.missing-finish-abort` | the connection closes after eight events (one for Gemini) | `cut_stream_after`, `end: abort` |
| `stream.error-midstream` | an error event arrives after eight events (one for Gemini) | `inject_error_event` |

## What libraries report (wiretruth)

Grades come from `results/<library>/<version>.json` at `v0.4.0`, the newest version of each library in the tag, run on
2026-11-13: any-llm 1.50.0, LiteLLM 1.109.1, pydantic-ai 2.68.0, TanStack AI 1.4.0 and Vercel AI SDK 7.0.171. A
fixture exists for up to six APIs (OpenAI Chat, OpenAI Responses, Anthropic, Gemini, Ollama, OpenAI-compatible
Ollama), so it has 25 or 30 cells.

| Fixture | Cells | PASS | LOSSY | WRONG | UNSUPPORTED |
|---|---|---|---|---|---|
| `stream.missing-finish` | 30 | 16 | 6 | 6 | 2 |
| `stream.missing-finish-abort` | 30 | 28 | 0 | 0 | 2 |
| `stream.error-midstream` | 25 | 22 | 0 | 1 | 2 |

The cells below PASS:

- `stream.missing-finish`, LOSSY: any-llm on anthropic, gemini, ollama, ollama-openai and openai-chat; TanStack AI on
  gemini. In `results/any-llm/1.50.0.json` the openai-chat cell has the partial text, no finish value, and
  `stream.completed` null, so nothing says the stream was incomplete. Its
  [cell page](https://kartsan03.github.io/wiretruth/cells/any-llm/stream.missing-finish/openai-chat.html) has the
  wire excerpt.
- `stream.missing-finish`, WRONG: pydantic-ai on anthropic, gemini, ollama-openai, openai-chat and openai-responses;
  LiteLLM on gemini.
- `stream.error-midstream`, WRONG: TanStack AI on gemini.
- `stream.missing-finish-abort`: no cell below PASS.

PASS means every MUST requirement of the scenario is met. LOSSY means the library doesn't contradict what happened,
but the fact is only in raw metadata or missing. WRONG means it reports something that contradicts what happened.
UNSUPPORTED means the library doesn't support that provider or feature, according to its documentation.

## What spans record (spantruth)

spantruth replays one of the three byte for byte. Its copy of `openai-responses/stream.missing-finish-abort.json` is
identical to the file at `v0.4.0` (checked with `cmp`). The scenario `openai/responses-stream-missing-finish-abort`
sends it through the `openai` SDK with one instrumentation library attached and grades the spans:

- `pass`: the span ends and records the error.
- `missing`: the span ends but has no `error.type`.
- `wrong`: the span ends with its status OK or unset where an error is expected.
- `lost`: the span never ends.

The other three columns are scenarios spantruth builds itself, not wiretruth's bytes. `openai/stream-dropped` and
`anthropic/stream-dropped` close the connection after three events, and `anthropic/stream-error-event` sends an
overloaded-error event. They are here because they are the same kind of failure.

| Library | `openai/responses-stream-missing-finish-abort` | `openai/stream-dropped` | `anthropic/stream-dropped` | `anthropic/stream-error-event` |
|---|---|---|---|---|
| ddtrace 4.16.3 | pass | pass | pass | pass |
| Langfuse 5.2.0 | wrong | wrong | - | - |
| mlflow-skinny 3.18.0 | missing | missing | wrong | wrong |
| OpenInference (openai 0.1.69, anthropic 3.3.0) | pass | pass | pass | pass |
| OpenLIT 1.45.0 | lost | lost | lost | lost |
| OpenLLMetry 0.62.4 | pass | wrong | lost | lost |
| OpenTelemetry GenAI 1.3b0 | pass | pass | pass | pass |
| sentry-sdk 2.74.0 | lost | lost | missing | missing |

`-` means spantruth has no Anthropic subject for that library. The `openai` SDK is 3.42.1 in every run except
OpenLIT's, which pins 2.54.0; the `anthropic` SDK is 2.4.0 except for OpenLIT's pin of 0.125.0. The SDK's own tracing
and the OpenTelemetry GenAI configuration with the SDK's tracing switched on pass both Anthropic scenarios as well.

## Where the two layers differ

On the one response both sides replay, the abort on the OpenAI Responses API, all five libraries grade PASS in
wiretruth, and four of the eight instrumentations pass in spantruth: Langfuse is `wrong`, mlflow-skinny `missing`,
OpenLIT and sentry-sdk `lost`. The libraries and the instrumentations are different software at different layers over
the same bytes, so one result does not predict the other. The runs also differ in date and in the `openai` SDK
underneath: wiretruth's three Python libraries ran on `openai` 3.50.0, spantruth pins 3.42.1, and the two JavaScript
libraries do not use the Python SDK at all.

## Not covered

spantruth does not replay `stream.missing-finish` or `stream.error-midstream`, so this page has no span result for
them. It has no scenario yet for a stream that ends cleanly without a finish signal.

Not measured: real provider calls, the libraries' current releases (`--latest`), retries, and any library or
instrumentation outside the two lists above.

## Reproduce

wiretruth, from a clone at the tag:

```
git clone --depth 1 --branch v0.4.0 https://github.com/kartsan03/wiretruth && cd wiretruth
python3 - <<'PY'
import json, collections
libs = {"any-llm": "1.50.0", "litellm": "1.109.1", "pydantic-ai": "2.68.0", "tanstack-ai": "1.4.0", "vercel-ai": "7.0.171"}
want = ("stream.missing-finish", "stream.missing-finish-abort", "stream.error-midstream")
grades = collections.defaultdict(collections.Counter)
for lib, version in libs.items():
    for cell in json.load(open(f"results/{lib}/{version}.json"))["cells"]:
        if cell["scenario"] in want:
            grades[cell["scenario"]][cell["grade"]] += 1
for scenario in want:
    print(scenario, dict(grades[scenario]))
PY
```

```
stream.missing-finish {'LOSSY': 6, 'PASS': 16, 'WRONG': 6, 'UNSUPPORTED': 2}
stream.missing-finish-abort {'PASS': 28, 'UNSUPPORTED': 2}
stream.error-midstream {'PASS': 22, 'UNSUPPORTED': 2, 'WRONG': 1}
```

spantruth 0.5.0 (the checkout is `cb3aaa4`, which carries a few unreleased changes), Python 3.12, pinned mode,
GenAI semantic conventions at `6fd0d763`, run on 2026-11-24:

```
git clone https://github.com/Plexito-de/spantruth && cd spantruth && git checkout cb3aaa4 && uv sync
uv run spantruth run --scenario openai/responses-stream-missing-finish-abort --scenario openai/stream-dropped \
  --scenario anthropic/stream-dropped --scenario anthropic/stream-error-event --out dropped.json
```

`dropped.json` in this directory is the output of that run.
