# Test your own LLM wrapper in five minutes

Many companies write a small wrapper around the OpenAI or Anthropic SDK, so that each call gets a span
with its model and its token counts. These wrappers usually look correct, and their tests pass, because
the tests cover a plain call that succeeds. The bugs are on the other paths: a stream, a cache hit, an
async client, a connection that drops.

spantruth runs those paths with no API key and no network, and compares what your wrapper reports with
what a local fake provider sent.

[`examples/in-house-wrapper`](../examples/in-house-wrapper) is a complete example: 40 lines that look fine.
It fails 14 of 17 scenarios:

| Result | Scenarios | What is wrong |
| --- | --- | --- |
| pass | `openai/basic`, `openai/tool-call`, `openai/retry-429` | The plain calls that its own tests would cover. |
| Ghost span | every `async` scenario | It patches the sync client only, so async calls get no span. |
| Vanishing tokens | `openai/stream`, `openai/stream-cache-hit` | A stream has no `usage` when the call returns, so no tokens are recorded. |
| Phantom tokens | `openai/cache-hit` | It adds cached tokens to a count that already includes them: 9475 for 5379. |
| False OK | `openai/stream-dropped` | The span ended before the stream failed, so a failed call looks fine. |

## 1. Add a directory for spantruth

In your repository, make a directory named `spantruth` with two files.

`subject.toml` says what to install. `--with` in step 2 replaces the version with your checkout.

```toml
title = "Acme's in-house LLM wrapper"
homepage = "https://example.com/acme-llm"
providers = ["openai"]
packages = ["acme-llm==0.1.0"]
```

`instrument.py` attaches your wrapper the way your application does.

```python
def instrument(tracer_provider, otlp_endpoint):
    from acme_llm import traced
    from openai.resources.chat.completions import Completions
    from opentelemetry import trace

    trace.set_tracer_provider(tracer_provider)
    Completions.create = traced(Completions.create)
```

spantruth then drives the SDK itself. If your application calls your wrapper rather than patching the
SDK, patch the SDK method with the same function here. That tests the code that writes your span.

## 2. Run it

You need [uv](https://docs.astral.sh/uv/). From the root of your repository:

```sh
uvx spantruth run --subject ./spantruth --with . --out results.json
uvx spantruth report results.json --html report.html
```

`report.html` shows each finding with its [bug class](https://plexito-de.github.io/spantruth/bugs.html), the
expected and the reported value, what it does to a cost figure, and the spans your wrapper exported.

To work through the example in this repository instead:

```sh
cd examples/in-house-wrapper
uvx spantruth run --subject ./spantruth --with . --out results.json
```

## 3. Keep it fixed

Record what fails today, commit the file, and let CI fail only on a change. The comment on each pull
request names the checks that moved.

```sh
uvx spantruth run --subject ./spantruth --with . --write-expect spantruth/expected.toml
```

```yaml
jobs:
  spantruth:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v5
      - uses: Plexito-de/spantruth@v0.4.1
        with:
          subject: ./spantruth
          with: .
          expect: spantruth/expected.toml
          comment: "true"
```

## If you use a library, not your own wrapper

Run this in your project, with no setup:

```sh
uvx spantruth doctor
```

It reads your lock file and runs the instrumentation libraries that it recognizes, at your versions.
