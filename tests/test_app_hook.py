"""A framework subject brings an app.py: the framework makes the call, not calls.py."""

import json
import shutil

import pytest

from spantruth import cli, driver, runner, subjects
from spantruth.mockserver import MockServer

# A minimal framework: it calls the SDK through calls.py and marks what the app saw, so a test can
# tell which code made the call.
APP = """
from spantruth import calls


def run(scenario, base_url, seen):
    calls.run(scenario, base_url, seen)
    seen.text = "app:" + seen.text
"""


def _framework(tmp_path, scenarios=("openai/basic", "openai/stream"), app=APP):
    target = tmp_path / "my-framework"
    shutil.copytree(subjects.SUBJECTS / "control-honest", target)
    listed = "" if scenarios is None else f"scenarios = {json.dumps(list(scenarios))}\n"
    (target / "subject.toml").write_text(
        'title = "t"\nhomepage = "https://example.org"\nproviders = ["openai"]\n' + listed
    )
    if app is not None:
        (target / "app.py").write_text(app)
    return target


def test_a_framework_subject_lists_its_scenarios(tmp_path):
    lib = subjects.load(str(_framework(tmp_path)))
    assert lib.app
    assert lib.scenarios == ("openai/basic", "openai/stream")
    assert lib.supports("openai/basic")
    assert not lib.supports("openai/tool-call")
    assert not lib.supports("anthropic/basic")


def test_a_library_subject_supports_every_scenario_of_its_providers():
    lib = subjects.load("openinference")
    assert (lib.app, lib.scenarios) == (False, ())
    assert lib.supports("openai/tool-call") and lib.supports("anthropic/stream")


def test_a_library_subject_is_unchanged_by_the_hook():
    libraries = [lib for lib in map(subjects.load, subjects.all_names()) if not lib.app]
    assert {"openinference", "control-honest", "control-faulty"} <= {lib.name for lib in libraries}
    for lib in libraries:
        assert lib.scenarios == (), lib.name
        assert runner.baseline_args(lib, ["openai/basic"], "M", "F") == [
            "baseline",
            "openai/basic",
            "--mock",
            "M",
            "--out",
            "F",
        ]


def test_a_framework_baseline_names_the_subject(tmp_path):
    lib = subjects.load(str(_framework(tmp_path)))
    args = runner.baseline_args(lib, ["openai/basic"], "M", "F")
    assert args[-2:] == ["--subject", str(lib.directory)]


def test_a_bad_scenario_list_is_one_clear_error(tmp_path):
    with pytest.raises(subjects.SubjectError, match="must list its scenarios"):
        subjects.load(str(_framework(tmp_path / "a", scenarios=None)))
    with pytest.raises(subjects.SubjectError, match="unknown scenario openai/nope"):
        subjects.load(str(_framework(tmp_path / "b", scenarios=["openai/nope"])))
    with pytest.raises(subjects.SubjectError, match="provider not listed"):
        subjects.load(str(_framework(tmp_path / "c", scenarios=["anthropic/basic"])))
    # A library that instruments the SDK may not leave a scenario out: that hides a red cell.
    with pytest.raises(subjects.SubjectError, match=r"only for a subject with an app\.py"):
        subjects.load(str(_framework(tmp_path / "d", app=None)))


def test_an_unsupported_scenario_has_no_cell(tmp_path):
    lib = subjects.load(str(_framework(tmp_path)))
    result = runner.run_subject(lib, ["openai/tool-call"], None, tmp_path, False, tmp_path)
    assert result["cells"] == {}
    assert "error" not in result
    assert runner.incomplete({"subjects": {lib.name: result}}) == []


def test_expect_with_only_unsupported_scenarios_is_a_usage_error(tmp_path, capsys):
    directory = _framework(tmp_path)
    code = cli.main(
        ["run", "--subject", str(directory), "--scenario", "openai/tool-call", "--expect", "x"]
    )
    assert code == 2
    assert "no selected scenario applies" in capsys.readouterr().err


def test_the_driver_runs_the_app_instead_of_calls_py(tmp_path):
    directory = _framework(tmp_path)
    out = tmp_path / "baseline.json"
    with MockServer() as mock:
        argv = ["baseline", "openai/basic", "--mock", mock.url, "--out", str(out)]
        assert driver.main([*argv, "--subject", str(directory)]) == 0
        framework = json.loads(out.read_text())["openai/basic"]
        assert driver.main(argv) == 0
        plain = json.loads(out.read_text())["openai/basic"]
    assert framework["exception"] is None
    assert framework["seen"]["text"] == "app:" + plain["seen"]["text"]
    assert framework["seen"]["usage"] == plain["seen"]["usage"]


def test_a_subject_without_an_app_runs_calls_py(tmp_path):
    assert driver._load_app(str(subjects.SUBJECTS / "openinference")) is None
    assert driver._load_app(None) is None


@pytest.mark.slow
@pytest.mark.external
@pytest.mark.timeout(600)
def test_a_framework_subject_runs_end_to_end(tmp_path):
    lib = subjects.load(str(_framework(tmp_path)))
    doc = runner.run([lib], ["openai/basic", "openai/stream", "openai/tool-call"], tmp_path / "v")
    cells = doc["subjects"]["my-framework"]["cells"]
    assert set(cells) == {"openai/basic", "openai/stream"}
    assert {sid: c["verdict"] for sid, c in cells.items()} == {
        "openai/basic": "pass",
        "openai/stream": "pass",
    }
    assert runner.incomplete(doc) == []
