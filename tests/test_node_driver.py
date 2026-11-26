"""Parity and verification tests for the Node.js driver."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from spantruth import driver, scenarios
from spantruth.mockserver import MockServer
from spantruth.receiver import Receiver

NODE_AVAILABLE = shutil.which("node") is not None
NODE_MODULES = Path("drivers/node/node_modules").is_dir()
SKIP_NODE = not (NODE_AVAILABLE and NODE_MODULES)

# Scenarios where Node.js / undici / JS SDKs raise a different exception class than Python / httpx.
EXPECTED_EXCEPTION_DIFFS: dict[str, tuple[str, str]] = {
    "openai/stream-dropped": ("openai.APIConnectionError", "openai.TypeError"),
    "anthropic/stream-error-event": ("anthropic.APIStatusError", "anthropic.APIError"),
    "anthropic/stream-dropped": ("httpx2.RemoteProtocolError", "anthropic.TypeError"),
    "openai/async-stream-dropped": ("openai.APIConnectionError", "openai.TypeError"),
    "anthropic/async-stream-error-event": ("anthropic.APIStatusError", "anthropic.APIError"),
    "anthropic/async-stream-dropped": ("httpx2.RemoteProtocolError", "anthropic.TypeError"),
    "openai/responses-stream-missing-finish-abort": (
        "openai.APIConnectionError",
        "openai.TypeError",
    ),
}


def _strip_nulls(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_nulls(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_nulls(v) for v in obj]
    return obj


@pytest.fixture
def mock():
    with MockServer() as server:
        yield server


def test_scenario_call_plans_serializable():
    plans = scenarios.all_call_plans()
    assert len(plans) == len(scenarios.SCENARIOS)
    encoded = json.dumps(plans)
    assert len(encoded) > 0
    for sid, sc in scenarios.SCENARIOS.items():
        plan = sc.call_plan()
        assert plan["id"] == sid
        assert plan["provider"] == sc.provider
        assert plan["request_model"] == sc.request_model
        assert plan["stream"] == sc.stream


def test_scenarios_json_matches_registered_scenarios():
    plans_file = Path("drivers/node/scenarios.json")
    assert plans_file.is_file()
    on_disk = json.loads(plans_file.read_text())
    expected = scenarios.all_call_plans()
    assert on_disk == expected


@pytest.mark.skipif(SKIP_NODE, reason="node or node_modules not available")
def test_node_driver_baseline_parity():
    all_ids = list(scenarios.SCENARIOS.keys())
    with MockServer() as node_mock, tempfile.NamedTemporaryFile(suffix=".json") as tmp:
        proc = subprocess.run(
            [
                "node",
                "drivers/node/driver.mjs",
                "baseline",
                *all_ids,
                "--mock",
                node_mock.url,
                "--out",
                tmp.name,
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"Node baseline failed: {proc.stderr}"
        node_results = json.loads(Path(tmp.name).read_text())

    with MockServer() as py_mock:
        for sid in all_ids:
            py_obs = driver.observe(sid, py_mock.url)
            node_obs = node_results[sid]

            assert node_obs["scenario"] == sid
            assert _strip_nulls(node_obs["seen"]) == _strip_nulls(py_obs["seen"]), (
                f"{sid}: seen mismatch"
            )
            if sid in EXPECTED_EXCEPTION_DIFFS:
                assert (py_obs["exception"], node_obs["exception"]) == EXPECTED_EXCEPTION_DIFFS[
                    sid
                ], f"{sid}: unexpected exception pair"
            else:
                assert node_obs["exception"] == py_obs["exception"], f"{sid}: exception mismatch"


@pytest.mark.skipif(SKIP_NODE, reason="node or node_modules not available")
def test_node_driver_instrumented_mode(mock):
    sc = scenarios.get("openai/basic")
    with Receiver() as rx, tempfile.TemporaryDirectory() as tmp:
        subject_dir = Path(tmp) / "dummy_subject"
        subject_dir.mkdir()
        (subject_dir / "instrument.mjs").write_text("export function instrument() { return []; }")

        out_file = Path(tmp) / "obs.json"
        proc = subprocess.run(
            [
                "node",
                "drivers/node/driver.mjs",
                "instrumented",
                sc.id,
                "--mock",
                mock.url,
                "--otlp",
                rx.url,
                "--subject",
                str(subject_dir),
                "--out",
                str(out_file),
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"Node instrumented failed: {proc.stderr}"
        obs = json.loads(out_file.read_text())

        assert obs["scenario"] == "openai/basic"
        assert obs["hang"] is False
        assert obs["exception"] is None
        assert obs["seen"]["text"] == "The capital of France is Paris."
        assert "parent" in obs
        assert obs["parent"]["trace_id"]
        assert obs["parent"]["span_id"]
        assert "probe" in obs
        assert obs["parent"]["span_id"] in obs["probe"]

        spans = list(rx.spans)
        assert any(s.name == "spantruth.scenario" for s in spans)
