"""`spantruth doctor`: read a project's versions, and say what they get wrong."""

import json
from dataclasses import replace

import pytest

from spantruth import cli, doctor, subjects

UV_LOCK = """
version = 1
[[package]]
name = "anthropic"
version = "2.5.0"
[[package]]
name = "OpenInference_Instrumentation.Anthropic"
version = "3.3.1"
[[package]]
name = "opentelemetry-sdk"
version = "1.45.2"
[[package]]
name = "rich"
version = "14.0.0"
"""


def project(tmp_path, **files):
    for name, text in files.items():
        (tmp_path / name.replace("__", ".")).write_text(text)
    return tmp_path


def test_doctor_reads_uv_lock_with_normalized_names(tmp_path):
    found = doctor.read_versions(project(tmp_path, uv__lock=UV_LOCK))
    assert found.source == "uv.lock"
    assert found.versions["openinference-instrumentation-anthropic"] == "3.3.1"


def test_doctor_reads_poetry_lock(tmp_path):
    text = '[[package]]\nname = "openlit"\nversion = "1.45.0"\n'
    assert doctor.read_versions(project(tmp_path, poetry__lock=text)).versions == {
        "openlit": "1.45.0"
    }


def test_doctor_reads_pipfile_lock(tmp_path):
    text = json.dumps({"default": {"openai": {"version": "==3.0.0"}}, "develop": {}})
    assert doctor.read_versions(project(tmp_path, Pipfile__lock=text)).versions == {
        "openai": "3.0.0"
    }


def test_doctor_reads_requirements_and_keeps_unpinned_names(tmp_path):
    text = (
        "# a comment\nopenai==3.1.0  # why\nanthropic>=2\n-r other.txt\n"
        "foo[bar]==1.0 ; python_version>'3'\n"
    )
    found = doctor.read_versions(project(tmp_path, requirements__txt=text))
    assert found.source == "requirements.txt"
    assert found.versions == {"openai": "3.1.0", "anthropic": None, "foo": "1.0"}


def test_doctor_reads_a_virtual_environment(tmp_path):
    info = tmp_path / ".venv" / "lib" / "python3.12" / "site-packages" / "openai-3.2.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Name: openai\nVersion: 3.2.0\n")
    found = doctor.read_versions(tmp_path)
    assert found.source == ".venv"
    assert found.versions == {"openai": "3.2.0"}


def test_doctor_reads_pyproject_names_last(tmp_path):
    text = '[project]\nname = "x"\ndependencies = ["openai>=3", "anthropic"]\n'
    found = doctor.read_versions(project(tmp_path, pyproject__toml=text))
    assert found.source == "pyproject.toml"
    assert found.versions == {"openai": None, "anthropic": None}


def test_a_lock_file_wins_over_requirements(tmp_path):
    found = doctor.read_versions(
        project(tmp_path, uv__lock=UV_LOCK, requirements__txt="openai==1.0\n")
    )
    assert found.source == "uv.lock"


def test_doctor_refuses_a_lock_it_cannot_parse(tmp_path):
    with pytest.raises(doctor.DoctorError, match=r"uv\.lock"):
        doctor.read_versions(project(tmp_path, uv__lock="[[package\n"))


def test_doctor_refuses_a_directory_with_nothing_to_read(tmp_path):
    with pytest.raises(doctor.DoctorError, match="no lock file"):
        doctor.read_versions(tmp_path)


def test_doctor_maps_packages_to_subjects():
    names = [s.name for s in doctor.match({"openinference-instrumentation-anthropic": "3.3.1"})]
    assert names == ["openinference"]


def test_doctor_never_matches_the_controls_or_the_demonstration_subject():
    versions = {
        "opentelemetry-instrumentation-genai-anthropic": "1.3b0",
        "opentelemetry-instrumentation-genai-openai": "1.3b0",
    }
    assert [s.name for s in doctor.match(versions)] == ["otel-official"]


def test_the_sdk_alone_counts_when_the_app_has_an_otel_sdk():
    assert [s.name for s in doctor.match({"anthropic": "2.5.0", "opentelemetry-sdk": "1.45"})] == [
        "anthropic-sdk"
    ]
    assert doctor.match({"anthropic": "2.5.0"}) == []


def test_a_library_that_covers_anthropic_replaces_the_sdk_subject():
    versions = {
        "anthropic": "2.5.0",
        "opentelemetry-sdk": "1.45",
        "openinference-instrumentation-anthropic": "3.3.1",
    }
    assert [s.name for s in doctor.match(versions)] == ["openinference"]


def test_text_says_when_it_could_not_apply_the_project_version():
    doc = result_doc()
    versions = {"anthropic": "2.5.0", "openinference-instrumentation-anthropic": None}
    text = doctor.render_text(
        doctor.Found("requirements.txt", versions), doctor.summarize(doc), "x"
    )
    assert "no version to apply for openinference-instrumentation-anthropic" in text


def test_text_names_a_meta_package_it_cannot_map_to_a_version():
    doc = result_doc()
    doc["subjects"]["openllmetry"] = doc["subjects"].pop("openinference")
    versions = {"traceloop-sdk": "0.62.4"}
    text = doctor.render_text(doctor.Found("uv.lock", versions), doctor.summarize(doc), "x")
    assert "no version to apply for traceloop-sdk" in text


def test_overrides_pin_every_package_the_subject_installs_and_nothing_else():
    versions = {
        "anthropic": "2.5.0",
        "openinference-instrumentation-anthropic": "3.3.1",
        "rich": "14.0.0",
        "openai": None,
    }
    specs = doctor.overrides([subjects.load("openinference")], versions)
    assert sorted(specs) == ["anthropic==2.5.0", "openinference-instrumentation-anthropic==3.3.1"]


def test_doctor_leaves_sdk_tracing_on_unless_the_caller_turned_it_off():
    subject = subjects.load("openinference")
    versions = {"anthropic": "2.5.0"}
    on = doctor.as_the_app_runs(subject, versions, environ={})
    assert on.env["ANTHROPIC_OPEN_TELEMETRY"] == "true"
    off = doctor.as_the_app_runs(subject, versions, environ={"ANTHROPIC_OPEN_TELEMETRY": "false"})
    assert off.env["ANTHROPIC_OPEN_TELEMETRY"] == "false"


def test_doctor_runs_only_the_providers_the_project_uses():
    ids = doctor.scenario_ids({"anthropic": "2.5.0"})
    assert ids and all(i.startswith("anthropic/") for i in ids)
    assert any(i.startswith("openai/") for i in doctor.scenario_ids({"foo": "1"}))


def result_doc():
    lost = {
        "id": "span.ended",
        "verdict": "lost",
        "expected": None,
        "got": None,
        "where": None,
        "detail": "never ended: chat <b>",
    }
    double = {
        "id": "tokens.single-report",
        "verdict": "wrong",
        "expected": 1,
        "got": 2,
        "where": None,
        "detail": "usage on 2 spans",
    }
    truth = {"request_model": "claude-sonnet-5-5", "input_tokens": 100, "output_tokens": 10}
    return {
        "spantruth": "0.4.0",
        "mode": "pinned",
        "created": "2026-11-06T00:00:00+00:00",
        "scenarios": {
            "anthropic/stream": {
                "title": "s",
                "catches": "",
                "provider": "anthropic",
                "truth": truth,
            },
            "anthropic/basic": {
                "title": "b",
                "catches": "",
                "provider": "anthropic",
                "truth": truth,
            },
        },
        "subjects": {
            "openinference": {
                "title": "OpenInference",
                "homepage": "https://example.com",
                "kind": "library",
                "ref": "openinference",
                "versions": {"anthropic": "2.5.0"},
                "cells": {
                    "anthropic/stream": {"verdict": "lost", "checks": [lost]},
                    "anthropic/basic": {"verdict": "wrong", "checks": [double]},
                },
            }
        },
    }


def test_summary_groups_by_class_with_the_cost_figure():
    (subject,) = doctor.summarize(result_doc())
    assert subject["name"] == "openinference"
    assert subject["scenarios"] == 2
    assert subject["failing"] == 2
    ghost, double = subject["classes"]
    assert (ghost["id"], ghost["name"], ghost["scenarios"]) == ("ST01", "Ghost span", 1)
    assert ghost["largest_cost_error"] == -100
    assert double["id"] == "ST02"
    assert double["largest_cost_error"] == 100


def test_a_class_figure_comes_from_that_class_alone():
    doc = result_doc()
    phantom = {
        "id": "output_tokens",
        "verdict": "wrong",
        "expected": 10,
        "got": 11,
        "where": None,
        "detail": "",
    }
    doc["subjects"]["openinference"]["cells"]["anthropic/basic"]["checks"].append(phantom)
    (subject,) = doctor.summarize(doc)
    by_id = {c["id"]: c for c in subject["classes"]}
    assert by_id["ST02"]["largest_cost_error"] == 100
    assert 0 < by_id["ST04"]["largest_cost_error"] < 50


def test_text_says_what_it_read_what_it_graded_and_what_it_did_not_run():
    found = doctor.Found("uv.lock", {"anthropic": "2.5.0"})
    text = doctor.render_text(found, doctor.summarize(result_doc()), "/tmp/r.json")
    assert "Read uv.lock" in text
    assert "ST01 Ghost span" in text
    assert "1 of 2 scenarios" in text
    assert "-100%" in text
    assert "ANTHROPIC_OPEN_TELEMETRY=false" in text
    assert "does not run your application" in text
    assert "/tmp/r.json" in text


def test_text_when_nothing_is_wrong():
    doc = result_doc()
    doc["subjects"]["openinference"]["cells"] = {
        "anthropic/basic": {"verdict": "pass", "checks": []}
    }
    text = doctor.render_text(doctor.Found("uv.lock", {}), doctor.summarize(doc), "/tmp/r.json")
    assert "no problem" in text.lower()


def test_two_libraries_get_a_warning_about_each_other():
    doc = result_doc()
    doc["subjects"]["openlit"] = replace_title(doc["subjects"]["openinference"])
    text = doctor.render_text(doctor.Found("uv.lock", {}), doctor.summarize(doc), "/tmp/r.json")
    assert "alone" in text


def replace_title(subject):
    return {**subject, "title": "OpenLIT", "ref": "openlit"}


def test_a_library_value_stays_on_one_line_in_the_text():
    doc = result_doc()
    doc["subjects"]["openinference"]["cells"]["anthropic/stream"]["checks"][0]["detail"] = (
        "a\nFake line"
    )
    text = doctor.render_text(doctor.Found("uv.lock", {}), doctor.summarize(doc), "/tmp/r.json")
    assert "\nFake line" not in text


def test_doctor_with_no_known_library_exits_2(tmp_path, capsys):
    project(tmp_path, requirements__txt="rich==14.0\n")
    assert cli.main(["doctor", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "no instrumentation library" in err
    assert "openllmetry" in err.lower() or "opentelemetry-instrumentation" in err


def test_doctor_with_a_broken_lock_exits_2(tmp_path, capsys):
    project(tmp_path, uv__lock="[[package\n")
    assert cli.main(["doctor", str(tmp_path)]) == 2


def test_exit_code_is_1_on_a_finding_and_0_when_clean():
    assert doctor.exit_code(result_doc(), doctor.summarize(result_doc())) == 1
    doc = result_doc()
    doc["subjects"]["openinference"]["cells"] = {
        "anthropic/basic": {"verdict": "pass", "checks": []}
    }
    assert doctor.exit_code(doc, doctor.summarize(doc)) == 0


def test_exit_code_is_3_when_the_run_is_incomplete():
    doc = result_doc()
    doc["subjects"]["openinference"]["error"] = "environment build failed"
    assert doctor.exit_code(doc, doctor.summarize(doc)) == 3


def test_a_subject_can_opt_out_of_detection():
    assert subjects.load("otel-official-sdk-on").detect == ()
    assert "openlit" in subjects.load("openlit").detect


def test_a_project_without_anthropic_keeps_the_subject_unchanged():
    subject = subjects.load("openinference")
    assert doctor.as_the_app_runs(subject, {"openai": "3"}, environ={}) == replace(subject)


@pytest.mark.slow
def test_doctor_end_to_end_finds_the_double_span(tmp_path, capsys):
    project(
        tmp_path,
        requirements__txt="anthropic==2.4.0\nopentelemetry-instrumentation-genai-anthropic==1.3b0\n",
    )
    out = tmp_path / "r.json"
    code = cli.main(["doctor", str(tmp_path), "--out", str(out), "--scenario", "anthropic/basic"])
    text = capsys.readouterr().out
    assert code == 1
    assert "ST02 Double span" in text
    assert out.exists()


@pytest.mark.slow
def test_doctor_end_to_end_openinference_hands_the_sdk_a_silent_span(tmp_path, capsys):
    """OpenInference 3.3 keeps one span per call with the SDK's tracing on, so no warning."""
    project(tmp_path, uv__lock=UV_LOCK)
    out = tmp_path / "r.json"
    assert (
        cli.main(["doctor", str(tmp_path), "--out", str(out), "--scenario", "anthropic/basic"]) == 0
    )
    assert "ST02" not in capsys.readouterr().out
