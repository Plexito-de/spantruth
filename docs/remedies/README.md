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
| ST03 Cache-blind tokens | It can flag the clear case. It cannot correct the count. | Upgrade the library. |
| ST04 Phantom tokens | No. The Collector does not know the true count. | Upgrade the library. |
| ST05 Vanishing tokens | No. A count that was never recorded cannot be restored. | Upgrade the library. |
| ST06 Hidden reasoning | No, for the same reason. | Upgrade the library. |
| ST07 False OK | No. A span that does not say it failed looks like a good span. | Upgrade the library. |
| ST08 False alarm | No. A recovered retry and a real failure look the same on the span. | Upgrade the library. |
| ST09 Silent stop | Yes, for the Anthropic SDK's own span. | Upgrade the library. |
| ST10 Observer effect | No. The bug is in the application, not in the span. | Upgrade the library. |

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

`error_mode: ignore` makes a statement skip a span that it cannot evaluate, such as a span without
the attribute. The span itself passes on unchanged.

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

## How the recipes are tested

[`tests/test_remedies.py`](../../tests/test_remedies.py) reads every `# recipe:` block on this
page. For each one it starts the Collector with an OTLP receiver, the recipe and a file exporter.
It sends spans as OTLP, and it checks the spans that the file exporter writes. The spans are the
span records of the [double-span measurement](../../results/2026-11-06-double-spans/README.md), as
each library exported them (`spans-x2-phoenix.jsonl`).

To run the test, download the Collector and check its sha256:

```sh
docs/remedies/fetch-otelcol.sh
uv run pytest -m collector
```

The test is skipped when the binary is absent. The `remedies` job in CI downloads it and runs the
test. `SPANTRUTH_OTELCOL` points the test to another binary. A new Collector version gets a new
run of the test before this page names it.
