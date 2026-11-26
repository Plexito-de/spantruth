"""`spantruth new subject` writes a subject that loads, lists and runs as it is."""

import json
from pathlib import Path

import pytest

from spantruth import cli, subjects


def test_a_new_built_in_subject_is_listed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subjects, "SUBJECTS", tmp_path)
    assert cli.main(["new", "subject", "my-library"]) == 0
    assert "spantruth run --subject my-library" in capsys.readouterr().out
    assert cli.main(["list"]) == 0
    listed = capsys.readouterr().out
    assert "my-library" in listed and "(anthropic, openai)" in listed
    s = subjects.load("my-library")
    assert (s.title, s.packages, s.kind) == ("my-library", (), "library")


def test_a_new_subject_elsewhere_loads_by_its_path(tmp_path, capsys):
    assert cli.main(["new", "subject", "spantruth", "--dir", str(tmp_path)]) == 0
    directory = tmp_path / "spantruth"
    assert f"--subject {directory}" in capsys.readouterr().out
    assert subjects.load(str(directory)).providers == ("anthropic", "openai")
    namespace: dict = {}
    exec((directory / "instrument.py").read_text(), namespace)
    assert namespace["instrument"](tracer_provider=None, otlp_endpoint="http://127.0.0.1:9") is None


def test_a_relative_directory_gets_a_reference_with_a_slash(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["new", "subject", "acme", "--dir", "."]) == 0
    assert "spantruth run --subject ./acme" in capsys.readouterr().out


def test_an_existing_subject_is_not_overwritten(tmp_path, capsys):
    (tmp_path / "taken").mkdir()
    assert cli.main(["new", "subject", "taken", "--dir", str(tmp_path)]) == 2
    assert "exists already" in capsys.readouterr().err
    assert list((tmp_path / "taken").iterdir()) == []


@pytest.mark.parametrize("name", ["_base", "My-Lib", "a/b", "x_y"])
def test_a_name_that_is_not_a_subject_name_is_refused(tmp_path, name, capsys):
    assert cli.main(["new", "subject", name, "--dir", str(tmp_path)]) == 2
    assert "is not a subject name" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.slow
@pytest.mark.external
@pytest.mark.timeout(600)
def test_the_template_runs_and_records_no_span(tmp_path):
    assert cli.main(["new", "subject", "template", "--dir", str(tmp_path)]) == 0
    out = tmp_path / "results.json"
    args = ["run", "--subject", str(tmp_path / "template"), "--scenario", "openai/basic"]
    assert cli.main([*args, "--out", str(out), "--cache", str(tmp_path / "venvs")]) == 0
    cell = json.loads(out.read_text())["subjects"]["template"]["cells"]["openai/basic"]
    assert [(c["id"], c["verdict"]) for c in cell["checks"]] == [
        ("app.unchanged", "pass"),
        ("span.recorded", "missing"),
    ]


def test_an_installed_spantruth_writes_a_new_subject_in_the_current_directory(
    tmp_path, monkeypatch
):
    # From a wheel the built-in subjects sit inside the package, in uv's cache. A subject written
    # there is lost to the user, so the default moves to the current directory.
    from spantruth import cli, paths

    monkeypatch.chdir(tmp_path)
    inside = paths.PACKAGE / "subjects"
    assert cli._new_subject_dir(None, inside) == tmp_path
    outside = tmp_path / "clone" / "subjects"
    assert cli._new_subject_dir(None, outside) == outside
    assert cli._new_subject_dir("elsewhere", inside) == Path("elsewhere")
