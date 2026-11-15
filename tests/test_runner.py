"""The runner's cross-cell rule, without subprocesses."""

from spantruth import runner, subjects
from spantruth.runner import _downgrade_unsupported_streams


def cell(*checks):
    verdicts = [v for _, v in checks]
    worst = "lost" if "lost" in verdicts else "missing" if "missing" in verdicts else "pass"
    return {"verdict": worst, "checks": [{"id": i, "verdict": v, "detail": ""} for i, v in checks]}


def test_early_exit_is_missing_not_lost_when_streams_are_not_instrumented():
    cells = {
        "openai/stream": cell(("span.recorded", "missing")),
        "openai/stream-early-exit": cell(("span.ended", "lost")),
        "openai/stream-early-exit-with": cell(("span.ended", "lost")),
        "openai/async-stream": cell(("span.recorded", "missing")),
        "openai/async-stream-early-exit": cell(("span.ended", "lost")),
    }
    _downgrade_unsupported_streams(cells)
    for sid in ("stream-early-exit", "stream-early-exit-with", "async-stream-early-exit"):
        assert cells[f"openai/{sid}"]["verdict"] == "missing", sid


def test_early_exit_stays_lost_when_the_full_stream_has_a_span():
    cells = {
        "openai/stream": cell(("span.parented", "pass")),
        "openai/stream-early-exit": cell(("span.ended", "lost")),
        "openai/stream-early-exit-with": cell(("span.ended", "lost")),
    }
    _downgrade_unsupported_streams(cells)
    assert cells["openai/stream-early-exit"]["verdict"] == "lost"
    assert cells["openai/stream-early-exit-with"]["verdict"] == "lost"


def test_a_subject_with_no_selected_scenario_is_not_an_incomplete_run(tmp_path):
    # An Anthropic-only subject and OpenAI scenarios: nothing to run, nothing missing.
    result = runner.run_subject(
        subjects.load("anthropic-sdk"), ["openai/basic"], None, tmp_path, False, tmp_path
    )
    assert result["cells"] == {}
    assert "error" not in result
    assert runner.incomplete({"subjects": {"anthropic-sdk": result}}) == []
