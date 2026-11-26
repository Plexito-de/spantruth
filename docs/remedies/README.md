# Fix or flag a bug class in an OpenTelemetry Collector

An OpenTelemetry Collector sits between your application and your backend. It can change a span
before the backend stores it. This page says, for each of the ten
[bug classes](https://plexito-de.github.io/spantruth/bugs.html), whether a Collector can correct
the span, flag it, or not help at all. Where it can, it gives a config that you can copy.

A Collector sees only the spans that reach it. It does not see the provider's response. So it
cannot restore a span that was never exported, and it cannot know the right token count. For
those classes, upgrade the library: `spantruth doctor` in your project names the version that
fixes it.

Every recipe below was tested on **otelcol-contrib 0.166.0** (linux amd64). The test feeds the
Collector the spans that spantruth recorded, and it checks what the file exporter writes. See
[How the recipes are tested](#how-the-recipes-are-tested).

## Each class at a glance

| Class | Can a Collector help? | What to do |
| --- | --- | --- |
| ST01 Ghost span | No. The span never reaches the Collector. | Upgrade the library. |
| ST02 Double span | Yes, when every call goes through one library on top of the Anthropic SDK. | [Drop the SDK's span](#st02-drop-the-sdks-span), or [strip its usage](#st02-keep-the-sdks-span-without-usage). Better: turn the SDK's tracing off. |
| ST03 Cache-blind tokens | It can flag the clear case. It cannot correct the count. | [Flag cache tokens above input tokens](#st03-flag-cache-tokens-above-input-tokens), then upgrade. |
| ST04 Phantom tokens | No. The Collector does not know the true count. | Upgrade the library. |
| ST05 Vanishing tokens | No. A count that was never recorded cannot be restored. | Upgrade the library. |
| ST06 Hidden reasoning | No, for the same reason. | Upgrade the library. |
| ST07 False OK | Partly. It can flag an error span that has no `error.type`, but the real type is lost. A span that does not say it failed looks like a good span. | [Flag a missing error type](#st07-flag-an-error-span-without-an-error-type), then upgrade. |
| ST08 False alarm | No. A recovered retry and a real failure look the same on the span. | Upgrade the library. |
| ST09 Silent stop | Yes, for the Anthropic SDK's own span. | [Copy the SDK's stop reason](#st09-copy-the-anthropic-sdks-stop-reason). |
| ST10 Observer effect | No. The library changes what the application receives. A Collector sees only the span, after the fact. | Upgrade the library. |

Two more recipes do not fix a bug class. They copy a count to the name that your backend reads:
[OpenInference names to GenAI names](#alias-openinference-token-counts-to-genai-names) and
[the cache write count for Phoenix and Opik](#alias-the-cache-write-count-for-phoenix-and-opik).

## How to use a recipe

Each recipe is a `processors:` block. Add it to your Collector config, and add the processor to
the `processors` list of your traces pipeline:

```yaml
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [filter/st02_drop_sdk_span]
      exporters: [otlphttp]
```

`error_mode: ignore` makes a statement skip a span that it cannot evaluate, such as a comparison
of a string with a number. The span itself passes on unchanged. A missing attribute is not an
error: it reads as `nil`.

## ST02: drop the SDK's span

The Anthropic SDK traces itself since 1.14.0. A library on top (OpenLLMetry, the OpenTelemetry
GenAI instrumentation, OpenInference before 3.1.1) writes a second span with the same token
counts. A backend that sums both counts the call twice. The
[measurement of 2026-11-06](../../results/2026-11-06-double-spans/README.md) shows 2,400 tokens
for a call of 1,200 with OpenLLMetry, on Phoenix and on Opik.

This recipe drops every span from the SDK's instrumentation scope:

```yaml
# recipe: st02-drop-sdk-span
processors:
  filter/st02_drop_sdk_span:
    error_mode: ignore
    traces:
      span:
        - instrumentation_scope.name == "com.anthropic.sdk.python"
```

**Use it only when a library reports every Anthropic call.** A call that goes through the SDK
alone then has no span at all. The test proves this: the SDK-only call disappears. The Collector
cannot see whether another span reports the same call, because the two spans are not always in
one trace. OpenLLMetry's two spans are in two traces when the app has no span of its own.
A span whose parent is the dropped span loses its parent in the trace view.

The better fix is at the source. Set `ANTHROPIC_OPEN_TELEMETRY=false`, or pass
`open_telemetry=False` to the client. OpenInference handles the SDK's span itself since
openinference-instrumentation-anthropic 3.1.1.

## ST02: keep the SDK's span without usage

This recipe keeps the SDK's span, with its HTTP details and its latency, and deletes its token
counts. The library's span keeps the counts.

```yaml
# recipe: st02-strip-sdk-usage
processors:
  transform/st02_strip_sdk_usage:
    error_mode: ignore
    trace_statements:
      - delete_matching_keys(span.attributes, "^gen_ai\\.usage\\.") where instrumentation_scope.name == "com.anthropic.sdk.python"
```

The same warning applies: a call through the SDK alone loses its token counts.

## ST03: flag cache tokens above input tokens

The GenAI conventions say that input tokens include cached tokens. Anthropic's `input_tokens`
does not, so a library must add the cache reads and writes. When the cache read count on a span
is larger than its input count, the library did not add it. The input count, and the cost, are
too low.

This recipe sets `spantruth.bug_class` to `ST03` on such a span. Filter on it in your backend to
find the calls whose cost is wrong.

```yaml
# recipe: st03-flag-cache-above-input
processors:
  transform/st03_flag_cache_above_input:
    error_mode: ignore
    trace_statements:
      - set(span.attributes["spantruth.bug_class"], "ST03") where span.attributes["gen_ai.usage.cache_read.input_tokens"] != nil and span.attributes["gen_ai.usage.cache_read.input_tokens"] > span.attributes["gen_ai.usage.input_tokens"]
```

It flags only the clear case. It does not flag a cache read that is smaller than the uncached
input, or a call that only writes the cache. It reads the GenAI names only. The recipe does not
correct the count, because the Collector cannot tell which part is missing. Upgrade the library.

## ST07: flag an error span without an error type

The GenAI conventions require `error.type` when a call ends in an error. Some libraries set the
span status to `ERROR` and leave `error.type` out. OpenLLMetry does so for a dropped Anthropic
stream (`anthropic/async-stream-dropped` in the
[results of 2026-11-03](../../results/2026-11-03-pinned.json)). A panel that groups errors by
`error.type` then does not show the call.

This recipe sets `error.type` to `_OTHER`, the value the conventions give when no other value
fits. The real type is lost, so the recipe flags the call and does not restore its cause:

```yaml
# recipe: st07-flag-missing-error-type
processors:
  transform/st07_flag_missing_error_type:
    error_mode: ignore
    trace_statements:
      - set(span.attributes["error.type"], "_OTHER") where span.status.code == STATUS_CODE_ERROR and span.attributes["error.type"] == nil
```

It never replaces an `error.type` that a span already has. It cannot help a span whose status
does not say that the call failed: that span looks like a good one.
## ST09: copy the Anthropic SDK's stop reason

The Anthropic SDK records the stop reason as `anthropic.message.stop_reason`, and not as
`gen_ai.response.finish_reasons`
([anthropic-sdk-python#2055](https://github.com/anthropics/anthropic-sdk-python/issues/2055)).
A backend that reads the GenAI name sees no finish reason. This recipe copies the value:

```yaml
# recipe: st09-copy-sdk-stop-reason
processors:
  transform/st09_copy_sdk_stop_reason:
    error_mode: ignore
    trace_statements:
      - set(span.attributes["gen_ai.response.finish_reasons"], [span.attributes["anthropic.message.stop_reason"]]) where span.attributes["anthropic.message.stop_reason"] != nil and span.attributes["gen_ai.response.finish_reasons"] == nil
```

It never replaces a finish reason that a span already has.

## Alias: OpenInference token counts to GenAI names

OpenInference writes `llm.token_count.*`. A backend that reads only the GenAI names
(`gen_ai.usage.*`) shows no tokens and no cost for these spans. This recipe copies each count to
its GenAI name, and keeps the original:

```yaml
# recipe: alias-openinference-to-genai
processors:
  transform/alias_openinference_to_genai:
    error_mode: ignore
    trace_statements:
      - set(span.attributes["gen_ai.usage.input_tokens"], span.attributes["llm.token_count.prompt"]) where span.attributes["llm.token_count.prompt"] != nil and span.attributes["gen_ai.usage.input_tokens"] == nil
      - set(span.attributes["gen_ai.usage.output_tokens"], span.attributes["llm.token_count.completion"]) where span.attributes["llm.token_count.completion"] != nil and span.attributes["gen_ai.usage.output_tokens"] == nil
      - set(span.attributes["gen_ai.usage.cache_read.input_tokens"], span.attributes["llm.token_count.prompt_details.cache_read"]) where span.attributes["llm.token_count.prompt_details.cache_read"] != nil and span.attributes["gen_ai.usage.cache_read.input_tokens"] == nil
      - set(span.attributes["gen_ai.usage.cache_write.input_tokens"], span.attributes["llm.token_count.prompt_details.cache_write"]) where span.attributes["llm.token_count.prompt_details.cache_write"] != nil and span.attributes["gen_ai.usage.cache_write.input_tokens"] == nil
      - set(span.attributes["gen_ai.usage.reasoning.output_tokens"], span.attributes["llm.token_count.completion_details.reasoning"]) where span.attributes["llm.token_count.completion_details.reasoning"] != nil and span.attributes["gen_ai.usage.reasoning.output_tokens"] == nil
```

Both conventions count cached tokens inside the input count, so no arithmetic is needed. A
GenAI name that a span already has keeps its value. Do not use this recipe for a backend that
reads both names and sums them.

**This recipe can create a double span.** With openinference-instrumentation-anthropic before
3.1.1 and the Anthropic SDK's tracing on, the SDK's span already has GenAI counts. After this
recipe, the OpenInference span has them too, and a backend that sums them counts the call twice.
The test shows it for the measured 3.1.0 call: 11,600 input tokens instead of 5,800. Use the
recipe only when one of these is true:

- The SDK's tracing is off (`ANTHROPIC_OPEN_TELEMETRY=false`).
- openinference-instrumentation-anthropic is 3.1.1 or later.
- An [ST02 recipe](#st02-keep-the-sdks-span-without-usage) runs before it in the same pipeline.
  The test runs `transform/st02_strip_sdk_usage` first and gets 5,800.

## Alias: the cache write count for Phoenix and Opik

The GenAI conventions name the cache write count `gen_ai.usage.cache_write.input_tokens`. The
Anthropic SDK and the OpenTelemetry GenAI instrumentation use that name. On 2026-11-06, Phoenix
21.0.0 and Opik 2.2.143 did not read it, and priced those tokens as plain input: $0.0096 instead
of $0.0102 for the [measured call](../../results/2026-11-06-double-spans/README.md). Both priced
OpenLLMetry's `gen_ai.usage.cache_creation.input_tokens` correctly. This recipe adds that name,
and keeps a value that a span already has:

```yaml
# recipe: alias-cache-write-for-phoenix-opik
processors:
  transform/alias_cache_write_for_phoenix_opik:
    error_mode: ignore
    trace_statements:
      - set(span.attributes["gen_ai.usage.cache_creation.input_tokens"], span.attributes["gen_ai.usage.cache_write.input_tokens"]) where span.attributes["gen_ai.usage.cache_write.input_tokens"] != nil and span.attributes["gen_ai.usage.cache_creation.input_tokens"] == nil
```

The test checks the attribute that the Collector writes. It did not send the result to Phoenix or
Opik again. Do not use this recipe for a backend that reads both cache write names and sums
them: it would count the cache writes twice.

## How the recipes are tested

[`tests/test_remedies.py`](../../tests/test_remedies.py) reads every `# recipe:` block on this
page. For each one it starts the Collector with an OTLP receiver, the recipe and a file exporter.
It sends spans as OTLP, and it checks the spans that the file exporter writes. The spans come
from two places:

- The span records of the
  [double-span measurement](../../results/2026-11-06-double-spans/README.md), as each library
  exported them (`spans-x2-phoenix.jsonl`).
- The attributes and the status that a [results document](../../results/2026-11-03-pinned.json)
  records for each check: the attribute name (`where`) and its value (`got`). The test builds a
  span from them.

To prove that a recipe keeps a value that is already set, the test also uses a few derived spans:
a recorded span with that one attribute set. Their project names start with `derived-`.

To run the test, download the Collector and check its sha256. The script downloads the linux
amd64 build only:

```sh
docs/remedies/fetch-otelcol.sh
uv run pytest -m collector
```

The test is skipped when the binary is absent. The `remedies` job in CI downloads it and runs the
test. `SPANTRUTH_OTELCOL` points the test to another binary. A new Collector version gets a new
run of the test before this page names it.
