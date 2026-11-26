# Grade your wrapper's spans in your own test suite

spantruth has a pytest plugin. A test makes a call against the local fake provider, and the plugin
grades the spans that your code exported against what the provider sent. It uses the same checks as
`spantruth run`, so a verdict means the same thing in both places. There is no API key, no network
and no subprocess.

Use it when your company has its own LLM wrapper and you want its spans tested next to its other
tests. To test a published library, or to get the HTML report and the CI comment, use
[`spantruth run`](test-your-wrapper.md).

## Install

```sh
pip install "spantruth[pytest]"
```

The extra installs the OpenTelemetry SDK and the OTLP exporter. pytest finds the plugin by itself.
To turn it off, run `pytest -p no:spantruth`.

## Write a test

Attach your wrapper the way your application does, then run a scenario:

```python
def test_a_stream_records_its_tokens(spantruth, monkeypatch):
    monkeypatch.setattr(Completions, "create", traced(Completions.create))
    spantruth.run("openai/stream").assert_pass()
```

`spantruth.run(scenario)` makes the call with the provider SDK, the same way `spantruth run` does.
When a check fails, the test fails with each failing check, the value that the wire carried, the
value that your span reported, and the rule that the check applies:

```text
openai/stream-dropped: wrong
  error.status: wrong, expected 'ERROR', got ['UNSET']
    rule: https://github.com/open-telemetry/semantic-conventions/blob/v1.44.0/docs/general/recording-errors.md
  error.type: missing
    rule: https://github.com/open-telemetry/semantic-conventions-genai/blob/6fd0d763a092db245a54104fc1124031eb3c51d2/model/gen-ai/spans.yaml#L3-L12
  spans received: chat gpt-5-mini, spantruth.scenario
```

When your application calls your wrapper and not the SDK, make the call yourself in a
`spantruth.call` block. Point your client at `call.base_url`:

```python
def test_our_client_reports_cached_tokens(spantruth):
    with spantruth.call("openai/cache-hit") as call:
        our_llm.ask("What is the capital of France?", base_url=call.base_url)
    call.result.assert_pass()
```

Make the call the way the scenario describes it: `call.scenario` holds the model, `stream`, and
`read_events` (where the app stops reading a stream). Close a stream inside the block, as your
application does. Catch the exception of an error scenario inside the block too.

## A complete example

[`examples/in-house-wrapper/tests/test_spans.py`](../examples/in-house-wrapper/tests/test_spans.py)
tests the example wrapper on every OpenAI scenario. spantruth's own test suite runs it, so it works as
shown. The wrapper has bugs on purpose. The test marks each known bug as `xfail(strict=True)`, so the
run passes today, and fails when a fix makes a scenario pass or a new bug appears:

```python
"""The spans of the Acme wrapper, graded by the spantruth pytest plugin. See docs/pytest.md.

Install: `pip install spantruth[pytest] openai`. Run: `pytest tests`.
"""

import openai
import pytest
from acme_llm import traced
from openai.resources.chat.completions import Completions

from spantruth.pytest_plugin import scenario_ids

# The scenarios the wrapper gets right today. Every other one shows a known bug, and is marked
# xfail(strict=True): when a fix makes one pass, the run fails until you move it here.
PASSING = {"openai/basic", "openai/tool-call", "openai/retry-429"}


@pytest.fixture(autouse=True)
def acme(monkeypatch):
    """Attach the wrapper the way the application does, for one test."""
    monkeypatch.setattr(Completions, "create", traced(Completions.create))


@pytest.mark.parametrize(
    "scenario",
    [
        sid if sid in PASSING else pytest.param(sid, marks=pytest.mark.xfail(strict=True))
        for sid in scenario_ids("openai")
    ],
)
def test_the_spans_match_the_wire(spantruth, scenario):
    spantruth.run(scenario).assert_pass()


def test_a_cache_hit_counts_the_cached_tokens_twice(spantruth):
    with spantruth.call("openai/cache-hit") as call:
        client = openai.OpenAI(base_url=call.base_url, api_key="test")
        client.chat.completions.create(model=call.scenario.request_model, messages=[])
    assert call.result.failing == {"input_tokens": "wrong", "cache_read_tokens": "missing"}
    (tokens,) = [c for c in call.result.checks if c.id == "input_tokens"]
    assert (tokens.expected, tokens.got) == (5379, 9475)
```

Run it from `examples/in-house-wrapper`:

```sh
pip install -e . "spantruth[pytest]"
pytest tests
```

## Reference

| Name | What it is |
| --- | --- |
| `spantruth` fixture | `run(scenario)`, `call(scenario)`, `base_url(scenario)` and `tracer_provider` |
| `spantruth.run(scenario)` | Makes the scenario's call with the SDK and returns a `Result`. An error scenario's exception is in `result.exception`, not raised. |
| `spantruth.call(scenario)` | A block for a call that your test makes. `call.result` is set when the block ends. |
| `Result.assert_pass()` | Fails the test when a check fails. |
| `Result.failing` | `{check: verdict}` for each failing check, as in an expectation file. |
| `Result.checks`, `Result.spans` | Every check record, and every span that arrived. |
| `scenario_ids(provider, api)` | Scenario IDs for `pytest.mark.parametrize`. |
| `spantruth_mock`, `spantruth_receiver` | The fake provider and the OTLP receiver, one per session. |

How the plugin works:

- **Tracing.** If your test suite has not set a global tracer provider, the plugin sets an SDK
  provider. If it has, the plugin adds one span processor to it. A wrapper that calls
  `trace.get_tracer()` then reports to the receiver with no change. A wrapper that takes a provider
  gets `spantruth.tracer_provider`. The provider stays for the whole session, because
  OpenTelemetry cannot unset a global provider. At the end of the session, the plugin's processor
  stops exporting.
- **Loading costs nothing.** pytest loads the plugin in every session where spantruth is installed.
  It imports the fake provider, the receiver and the OpenTelemetry SDK only when a test uses a
  fixture. If the SDK is not installed, that test stops with a message that says what to install.
- **One test at a time.** A result holds the spans that arrived during its call. Do not run two
  calls at the same time in one process, for example from threads.
- **The same environment as a subject.** The plugin sets the variables from `subjects/_base.toml`
  that you have not set. For example, it turns off the Anthropic SDK's own spans
  (`ANTHROPIC_OPEN_TELEMETRY=false`), so your code is graded on its own spans.
  They stay set for the whole session, and go away when it ends.
- **One check is `n/a`.** `app.unchanged` compares an instrumented run with an uninstrumented run in
  another process. A test has only the instrumented run. Use `spantruth run` for that check.
