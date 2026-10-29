"""Requirement resolution, including `--with` overrides, without building an environment."""

from spantruth import cli, subjects

PR_HEAD = (
    "opentelemetry-instrumentation-anthropic @ git+https://github.com/traceloop/openllmetry"
    "@refs/pull/4377/head#subdirectory=packages/opentelemetry-instrumentation-anthropic"
)


def test_package_name_normalizes_every_requirement_form():
    assert subjects.package_name("openai==3.28.0") == "openai"
    assert subjects.package_name("Foo_Bar.baz>=1") == "foo-bar-baz"
    assert subjects.package_name("pkg[extra]==1") == "pkg"
    assert subjects.package_name(PR_HEAD) == "opentelemetry-instrumentation-anthropic"


def test_an_override_replaces_only_the_package_it_names():
    lib = subjects.load("openllmetry")
    pinned = subjects.requirements(lib, latest=False)
    got = subjects.requirements(lib, latest=False, overrides=(PR_HEAD,))
    assert PR_HEAD in got
    assert "opentelemetry-instrumentation-anthropic==0.62.4" not in got
    assert [r for r in got if r != PR_HEAD] == [
        r for r in pinned if not r.startswith("opentelemetry-instrumentation-anthropic")
    ]


def test_openlit_is_pinned_and_tracked():
    lib = subjects.load("openlit")
    assert lib.title == "OpenLIT"
    assert lib.providers == ("openai", "anthropic")
    assert lib.homepage == "https://github.com/openlit/openlit"
    assert lib.overrides is True
    assert "openlit==1.45.0" in subjects.requirements(lib, latest=False)
    latest = subjects.requirements(lib, latest=True)
    assert "openlit" in latest
    assert "openlit==1.45.0" not in latest


def test_environment_uses_overrides_only_when_opted_in(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path

    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["uv", "venv"]:
            Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
        stdout = "openai==3.28.0\n" if kwargs.get("capture_output") else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    openlit = subjects.load("openlit")
    subjects.environment(openlit, cache=tmp_path)

    install_cmds = [c for c in calls if c[:3] == ["uv", "pip", "install"] and "--no-deps" not in c]
    assert len(install_cmds) == 1
    install_cmd = install_cmds[0]
    assert "--overrides" in install_cmd
    overrides_file = Path(install_cmd[install_cmd.index("--overrides") + 1])
    assert overrides_file.is_file()
    overrides_lines = overrides_file.read_text().splitlines()
    assert "openai==3.28.0" in overrides_lines
    assert "anthropic==1.13.0" in overrides_lines

    calls.clear()
    other = subjects.load("control-honest")
    assert other.overrides is False
    subjects.environment(other, cache=tmp_path)

    other_install_cmds = [
        c for c in calls if c[:3] == ["uv", "pip", "install"] and "--no-deps" not in c
    ]
    assert len(other_install_cmds) == 1
    assert "--overrides" not in other_install_cmds[0]


def test_an_override_for_a_package_nobody_installs_is_refused(capsys):
    code = cli.main(["run", "--subject", "control-honest", "--with", PR_HEAD, "--out", "/dev/null"])
    assert code == 2
    assert "no selected subject installs" in capsys.readouterr().err


def test_cli_without_subjects_points_to_a_clone(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subjects, "SUBJECTS", tmp_path / "missing")
    assert cli.main(["list"]) == 2
    assert "git clone https://github.com/Plexito-de/spantruth" in capsys.readouterr().err
