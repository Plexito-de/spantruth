"""Parity and verification tests for the Node.js driver."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from spantruth import scenarios
from spantruth.mockserver import MockServer
from spantruth.receiver import Receiver

NODE_AVAILABLE = shutil.which("node") is not None
NODE_MODULES = Path("drivers/node/node_modules").is_dir()
SKIP_NODE = not (NODE_AVAILABLE and NODE_MODULES)


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
@pytest.mark.parametrize(
    "sid",
    [
        "openai/basic",
        "openai/stream",
        "anthropic/basic",
        "anthropic/stream",
    ],
)
def test_node_driver_baseline_parity(sid, mock):
    sc = scenarios.get(sid)
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
        proc = subprocess.run(
            [
                "node",
                "drivers/node/driver.mjs",
                "baseline",
                sid,
                "--mock",
                mock.url,
                "--out",
                tmp.name,
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"Node baseline failed: {proc.stderr}"
        res = json.loads(Path(tmp.name).read_text())

    assert sid in res
    obs = res[sid]
    assert obs["scenario"] == sid
    assert obs["exception"] is None
    seen = obs["seen"]
    assert seen["text"] == "The capital of France is Paris."
    if sc.stream:
        assert seen["events"] > 0
    else:
        assert seen["events"] == 0
    assert seen["response_id"] is not None


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
