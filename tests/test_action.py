"""The GitHub Action, run outside GitHub with the variables a runner sets."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
ACTION = yaml.safe_load((REPO / "action.yml").read_text())


def step(step_id: str) -> dict:
    (found,) = [s for s in ACTION["runs"]["steps"] if s.get("id") == step_id]
    return found


def test_inputs_reach_the_script_only_through_the_environment():
    script = step("run")["run"]
    assert "${{" not in script
    assert set(step("run")["env"]) >= {"SUBJECT", "WITH", "SCENARIO", "EXPECT", "LATEST", "COMMENT"}


def test_every_action_the_action_uses_is_pinned_to_a_commit():
    for s in ACTION["runs"]["steps"]:
        if "uses" in s:
            ref = s["uses"].split("@", 1)[1]
            assert len(ref) == 40 and int(ref, 16) >= 0, s["uses"]


def run_action(tmp_path: Path, runner_temp: Path, **inputs: str) -> tuple[int, dict[str, str], str]:
    """Run the action's script as a runner would. Return its status, outputs and step summary.

    Every step of a job shares one RUNNER_TEMP, so two runs in one job must not collide there.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    runner_temp.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "run.sh"
    script.write_text(step("run")["run"])
    output, summary = tmp_path / "output", tmp_path / "summary.md"
    output.touch()
    summary.touch()
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_ACTION_PATH": str(REPO),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
        **{k.upper(): v for k, v in inputs.items()},
    }
    for name in ("SUBJECT", "WITH", "SCENARIO", "EXPECT", "LATEST", "ARTIFACT_NAME", "COMMENT"):
        env.setdefault(name, "")
    proc = subprocess.run(["bash", str(script)], env=env, cwd=REPO, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
    return int(outputs["exit-code"]), outputs, summary.read_text()


def fake_gh(tmp_path: Path, existing: str = "", fail_post: bool = False) -> tuple[Path, Path]:
    """A `gh` that records its arguments and answers the comment listing with `existing`."""
    calls = tmp_path / "calls"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    fail = "exit 1" if fail_post else ":"
    gh.write_text(
        f"""#!/bin/bash
echo "$*" >> {calls}
if [[ "$*" == *--paginate* ]]; then echo "{existing}"; exit 0; fi
if [[ "$*" == *"-X POST"* ]]; then {fail}; fi
exit 0
"""
    )
    gh.chmod(0o755)
    return bin_dir, calls


def run_comment(tmp_path: Path, bin_dir: Path) -> subprocess.CompletedProcess:
    body = tmp_path / "comment.md"
    body.write_text("<!-- spantruth:x -->\nhello")
    script = tmp_path / "comment.sh"
    script.write_text(step("comment")["run"])
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GH_TOKEN": "t",
        "PR_NUMBER": "7",
        "REPOSITORY": "o/r",
        "COMMENT_FILE": str(body),
        "MARKER": "<!-- spantruth:x -->",
    }
    return subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)


def test_the_comment_step_creates_a_comment_when_there_is_none(tmp_path):
    bin_dir, calls = fake_gh(tmp_path)
    assert run_comment(tmp_path, bin_dir).returncode == 0
    assert "-X POST repos/o/r/issues/7/comments" in calls.read_text()


def test_the_comment_step_updates_its_own_comment(tmp_path):
    bin_dir, calls = fake_gh(tmp_path, existing="123")
    assert run_comment(tmp_path, bin_dir).returncode == 0
    text = calls.read_text()
    assert "-X PATCH repos/o/r/issues/comments/123" in text
    assert "-X POST" not in text


def test_a_read_only_token_warns_and_never_fails_the_job(tmp_path):
    bin_dir, _ = fake_gh(tmp_path, fail_post=True)
    proc = run_comment(tmp_path, bin_dir)
    assert proc.returncode == 0
    assert "::warning::" in proc.stdout


def test_the_comment_step_takes_its_values_from_the_environment_only():
    comment = step("comment")
    assert "${{" not in comment["run"]
    assert set(comment["env"]) >= {"GH_TOKEN", "PR_NUMBER", "REPOSITORY", "COMMENT_FILE", "MARKER"}


@pytest.mark.slow
@pytest.mark.external
@pytest.mark.timeout(600)
def test_the_action_compares_with_an_expectation_file(tmp_path):
    empty = tmp_path / "empty.toml"
    empty.write_text("")
    common = {
        "subject": "control-faulty",
        "scenario": "openai/stream-no-usage\n\nanthropic/basic\n",
    }
    runner_temp = tmp_path / "runner"
    code, outputs, summary = run_action(
        tmp_path / "a", runner_temp, expect="subjects/control-faulty/expected.toml", **common
    )
    assert code == 0, summary
    assert outputs["artifact-name"] == "spantruth-control-faulty"
    assert Path(outputs["results"]).is_file()
    assert (Path(outputs["results"]).parent / "report.html").is_file()
    code, _, summary = run_action(
        tmp_path / "b", runner_temp, expect=str(empty), artifact_name="x", **common
    )
    assert code == 1
    assert "new failure" in summary and "openai/stream-no-usage" in summary


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_a_failed_install_still_reports_exit_code_3(tmp_path):
    script = tmp_path / "run.sh"
    script.write_text(step("run")["run"])
    output = tmp_path / "output"
    output.touch()
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_ACTION_PATH": str(tmp_path / "no-such-checkout"),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "SUBJECT": "control-honest",
        "WITH": "",
        "SCENARIO": "",
        "EXPECT": "",
        "LATEST": "",
        "ARTIFACT_NAME": "",
        "COMMENT": "",
    }
    proc = subprocess.run(["bash", str(script)], env=env, cwd=REPO, capture_output=True, text=True)
    assert proc.returncode != 0
    assert "exit-code=3" in output.read_text()
