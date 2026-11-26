"""The pytest plugin grades spans in the test process with the same checks as `spantruth run`.

The proof is the controls again: the reference instrumentation passes every scenario in process, and
with its faults on it fails exactly the checks in control-faulty's expected.toml.
"""

import os
import subprocess
import sys
from pathlib import Path

import anthropic
import anthropic.resources.messages as am
import openai
import openai.resources.chat.completions as oc
import openai.resources.responses as orr
import pytest

from spantruth import expectations, reference, scenarios
from spantruth.inprocess import NOT_COMPARED
from spantruth.pytest_plugin import scenario_ids
from spantruth.subjects import SUBJECTS

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "in-house-wrapper"
PATCHED = (
    oc.Completions,
    oc.AsyncCompletions,
    orr.Responses,
    orr.AsyncResponses,
    am.Messages,
    am.AsyncMessages,
)


@pytest.fixture
def attach(monkeypatch, spantruth):
    """Attach the reference instrumentation for one test, and take it off again after."""

    def attach_reference(faults=frozenset()):
        for cls in PATCHED:
            monkeypatch.setattr(cls, "create", cls.create)
        reference.instrument(spantruth.tracer_provider, faults=faults)

    return attach_reference


# A content cell sets a subject's switch, which the plugin has not got, and a scenario with
# several calls needs the driver's call windows.
IN_PROCESS = [sid for sid, sc in scenarios.SCENARIOS.items() if not (sc.content or sc.calls)]


def test_a_content_scenario_is_refused_in_process(spantruth):
    with pytest.raises(ValueError, match="content switch"):
        spantruth.run("openai/content-on")


def test_a_scenario_with_several_calls_is_refused_in_process(spantruth):
    with pytest.raises(ValueError, match="several calls"):
        spantruth.run("openai/agent-loop")


def test_the_reference_passes_every_scenario_in_process(spantruth, attach):
    attach()
    for sid in IN_PROCESS:
        result = spantruth.run(sid)
        assert result.failing == {}, result.explain()
        (app,) = [c for c in result.checks if c.id == "app.unchanged"]
        assert (app.verdict, app.detail) == ("n/a", NOT_COMPARED)


def test_the_faulty_reference_fails_exactly_its_planted_faults(spantruth, attach):
    faulty = SUBJECTS / "control-faulty"
    namespace: dict = {}
    exec((faulty / "instrument.py").read_text(), namespace)
    attach(namespace["FAULTS"])
    got = {sid: f for sid in IN_PROCESS if (f := spantruth.run(sid).failing)}
    expected = expectations.load(faulty / "expected.toml")
    assert got == {sid: f for sid, f in expected.items() if sid in IN_PROCESS}


def test_no_wrapper_is_a_ghost_span(spantruth):
    result = spantruth.run("openai/basic")
    assert result.failing == {"span.recorded": "missing"}
    with pytest.raises(AssertionError, match=r"span\.recorded: missing"):
        result.assert_pass()


def test_a_call_the_test_makes_itself_is_graded(spantruth, attach):
    attach(frozenset({"anthropic-raw-input"}))
    with spantruth.call("anthropic/cache-hit") as call:
        client = anthropic.Anthropic(base_url=call.base_url, api_key="test", max_retries=0)
        client.messages.create(model=call.scenario.request_model, max_tokens=8, messages=[])
    assert call.result.failing == {"input_tokens": "wrong"}
    message = call.result.explain()
    assert "input_tokens: wrong, expected 5802, got 1187" in message
    assert "cache reads and writes left out" in message
    assert "rule: https://" in message


def test_an_error_the_test_catches_is_graded(spantruth, attach):
    attach(frozenset({"errors-as-success"}))
    with spantruth.call("openai/rate-limited") as call:
        client = openai.OpenAI(base_url=call.base_url, api_key="test", max_retries=0)
        with pytest.raises(openai.RateLimitError):
            client.chat.completions.create(model="gpt-5-mini", messages=[])
    assert call.result.failing == {"error.status": "wrong", "error.type": "missing"}


def test_scenario_ids_filter_by_provider_and_api():
    openai_ids = scenario_ids("openai")
    assert "openai/basic" in openai_ids and "anthropic/basic" not in openai_ids
    assert scenario_ids("openai", api="responses") == [
        "openai/responses-stream-missing-finish-abort"
    ]
    assert scenario_ids() == list(scenarios.SCENARIOS)


@pytest.mark.slow
def test_the_documented_example_runs(tmp_path):
    """docs/pytest.md shows this file. It passes where the wrapper is right, and fails strictly
    (xfail) where it has its bugs, so a fixed bug or a check that stops firing fails the run."""
    env = os.environ | {"PYTHONPATH": str(EXAMPLE)}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(EXAMPLE / "tests")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # Three scenarios pass, and the explicit cache-hit test. Every other OpenAI scenario is a bug.
    xfailed = len(scenario_ids("openai")) - 3
    assert f"4 passed, {xfailed} xfailed" in proc.stdout, proc.stdout


def test_the_documentation_shows_the_example_as_it_runs():
    doc = (REPO / "docs" / "pytest.md").read_text()
    assert (EXAMPLE / "tests" / "test_spans.py").read_text() in doc


def test_loading_the_plugin_imports_nothing_heavy():
    """pytest loads the plugin in every session where spantruth is installed. Loading it must
    cost nothing, and a broken import further in must not break an unrelated test suite."""
    probe = (
        "import sys, spantruth.pytest_plugin; "
        "print(sorted(m for m in ('google.protobuf', 'spantruth.scenarios', "
        "'spantruth.inprocess', 'opentelemetry') if m in sys.modules))"
    )
    proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert proc.stdout.strip() == "[]"
