# The scenario pack: every scenario as data

`spantruth export` writes every scenario as JSON. Use it when you build a driver or a test harness
in another language, for example for JavaScript instrumentation libraries. You need no Python
code from spantruth to read it.

```sh
spantruth export --out scenarios.pack.json       # the full pack
spantruth export --calls-only --out plans.json   # only the call plans, keyed by scenario ID
```

The output has no date in it, so one spantruth version always writes the same bytes.

## The format

```json
{
 "format": 1,
 "spantruth": "0.5.0",
 "semconv_genai": "6fd0d763",
 "scenarios": {
  "anthropic/stream-dropped": {
   "id": "anthropic/stream-dropped",
   "provider": "anthropic",
   "request_model": "claude-sonnet-5-5",
   "stream": true,
   "stream_usage": true,
   "tools": false,
   "read_events": null,
   "with_block": false,
   "asynchronous": false,
   "api": "chat",
   "entrypoint": "create",
   "retries": 0,
   "title": "Connection drops mid-stream",
   "catches": "an error swallowed into a cut-off answer",
   "tags": [],
   "content": "",
   "depends_on": null,
   "path": "/s/anthropic--stream-dropped",
   "responses": [{"status": 200, "headers": {}, "events": ["..."], "drop_after": 3}],
   "truth_response": 0,
   "truth": {"request_model": "claude-sonnet-5-5", "error": "connection dropped", "...": "..."}
  }
 }
}
```

Each scenario has three parts.

**The call plan** is how the application calls the provider SDK:

| Key | Meaning |
| --- | --- |
| `id`, `provider` | The scenario ID, and `openai` or `anthropic`. |
| `request_model` | The model that the app asks for. |
| `api` | For OpenAI: `chat` (Chat Completions) or `responses` (the Responses API). |
| `stream`, `stream_usage` | A streamed call. `stream_usage` false leaves out OpenAI's `include_usage`. |
| `tools` | The app sends two tool definitions, `get_weather` and `get_time`. |
| `read_events` | The app stops reading the stream after this many events and closes it. `null`: it reads to the end. |
| `with_block` | The app leaves a `with` block instead of calling `close()`. |
| `asynchronous` | The app uses the async client. |
| `entrypoint` | `create`, or `with_streaming_response` for OpenAI's helper. |
| `retries` | The SDK's retry count. |

**The responses** are what the fake provider sends, one per request, in order. A scenario with
`retries` has more than one: first an error, then the success. Each response has a `status`,
`headers`, and either a JSON `body` or a list of `events`. A stream with `drop_after` closes the
connection after that many events, without the end of the chunked body.

An event is `{"event": NAME or null, "data": OBJECT or STRING}`. On the wire, each event is:

```text
event: NAME          (only when NAME is not null)
data: DATA           (the object as JSON, or the string as it is, such as [DONE])
                     (an empty line)
```

The body and each event keep the key order of the wire. `tests/test_pack.py` checks that the
mock server sends exactly these bytes for every scenario.

**The truth** is what a correct instrumentation reports for the response at index
`truth_response`. `null` means that the wire carries no value for that fact, so the fact is not
graded. Token totals follow the OpenTelemetry GenAI rules: input tokens include cached tokens,
and output tokens include reasoning tokens. `unread_counts` are counts that only the unread part of
a stream carries. `no_usage` true means the wire carries no usage, so any token count is an
estimate.

The other keys: `title` and `catches` describe the scenario. `tags` holds `recorded` for a
scenario replayed from a recorded stream. `depends_on` names the full stream whose verdict decides
an early-exit cell. `path` is the path prefix on spantruth's mock server. `content` is `off` or
`on` for a content scenario, which runs with the library's own switch for prompt and
completion text set that way, and empty for every other scenario.
`calls` lists each call of a scenario that makes more than one, such as an agent loop:
its `request_model` and its `response`. The mock server picks a call's response by the
request's model and by how many assistant messages the request sends back.

## Use the pack with spantruth's own mock server

A driver in another language does not have to serve the responses itself. `spantruth run` starts
the mock server, and the driver points the SDK at `<mock URL><path>`, plus `/v1` for OpenAI. Then
the driver needs only the call plan. The responses and the truth are there for a harness that runs
without spantruth.

## A driver that reads only call plans

`spantruth export --calls-only` writes the call plans alone: one object keyed by scenario ID, each
value with the twelve keys of the call-plan table above. A driver that looks a plan up by
`data[scenarioId]` reads this file. It does not read the full pack as it is, because the pack keeps
its scenarios under `scenarios`. Give such a driver the `--calls-only` file, or one entry of the
pack: an entry has every key of a call plan, plus more that the driver can ignore. Compare a file
with the export as JSON, not as bytes: the indent and the order of the keys can differ.

## What else the mock server does

A driver that uses spantruth's mock server gets two things that the pack does not list:

- Every response also has the header `x-request-id: req_spantruth`. A JSON body has
  `content-type: application/json` and `content-length`. A stream has
  `content-type: text/event-stream` and `transfer-encoding: chunked`.
- A scenario with more than one response gives them in order, one per request, and starts again
  after the last. When the request has the header `x-stainless-retry-count: N`, which the OpenAI and
  Anthropic SDKs send, the server gives response N instead (the last one, when N is larger).

## Versions

`format` is the version of the shape. It goes up when a key changes meaning, changes type, or goes
away. A new key does not change it, so a reader must ignore keys that it does not know. A scenario
ID never changes meaning, so a scenario's entry describes the same call in every version.
