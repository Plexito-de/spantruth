"""`spantruth doctor`: read a project's versions, and say what they get wrong."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from spantruth import cli, doctor, doctor_base, report, subjects

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


def test_a_library_with_other_uses_needs_a_provider_sdk_too():
    assert doctor.match({"sentry-sdk": "2.74.0", "ddtrace": "4.16.3", "mlflow": "3.18.0"}) == []
    names = {
        s.name
        for s in doctor.match(
            {"sentry-sdk": "2.74.0", "ddtrace": "4.16.3", "mlflow": "3.18.0", "openai": "3.0"}
        )
    }
    assert names == {"sentry", "ddtrace", "mlflow"}


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


def test_the_exporter_follows_the_sdk_version_of_the_project():
    # The harness needs the OTLP exporter, and the exporter pins the SDK of its own version. A
    # project that pins only the SDK, or uses the gRPC exporter, has no exporter version to apply.
    specs = doctor.overrides([subjects.load("openinference")], {"opentelemetry-sdk": "1.44.0"})
    assert sorted(specs) == [
        "opentelemetry-exporter-otlp-proto-http==1.44.0",
        "opentelemetry-sdk==1.44.0",
    ]


def test_an_exporter_the_project_pins_itself_is_left_alone():
    versions = {
        "opentelemetry-sdk": "1.44.0",
        "opentelemetry-exporter-otlp-proto-http": "1.44.1",
    }
    specs = doctor.overrides([subjects.load("openinference")], versions)
    assert "opentelemetry-exporter-otlp-proto-http==1.44.1" in specs


def test_doctor_leaves_sdk_tracing_on_when_a_dependency_brings_anthropic():
    subject = subjects.load("otel-official")
    on = doctor.as_the_app_runs(subject, {"langchain-anthropic": "1.7.4"}, environ={})
    assert on.env["ANTHROPIC_OPEN_TELEMETRY"] == "true"


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


FIXED = {
    "subject": "openinference",
    "scenarios": ["anthropic/stream"],
    "check": "span.ended",
    "status": "fixed-upstream",
    "url": "https://example.com/issues/1",
    "note": "Fixed in openinference-instrumentation-anthropic 3.1.0.",
}
REPORTED = {
    "subject": "openinference",
    "scenarios": ["anthropic/basic"],
    "check": "tokens.single-report",
    "status": "reported",
    "url": "https://example.com/issues/2",
    "note": "Reported upstream.",
}


@pytest.fixture
def known(monkeypatch):
    monkeypatch.setattr(report, "known_issues", lambda: [FIXED, REPORTED])


def test_a_fixed_upstream_finding_prints_its_note_with_the_link(known):
    text = doctor.render_text(doctor.Found("uv.lock", {}), doctor.summarize(result_doc()), "x")
    assert f"Fixed upstream: {FIXED['note']} {FIXED['url']}" in text
    assert f"Tracked at {FIXED['url']}" not in text


def test_a_reported_finding_keeps_tracked_at(known):
    text = doctor.render_text(doctor.Found("uv.lock", {}), doctor.summarize(result_doc()), "x")
    assert f"Tracked at {REPORTED['url']}" in text
    assert REPORTED["note"] not in text


def test_json_keeps_upstream_as_urls_with_status_and_note_beside_it(known):
    (subject,) = doctor.summarize(result_doc())
    ghost, double = subject["classes"]
    assert ghost["upstream"] == [FIXED["url"]]
    assert ghost["upstream_issues"] == [
        {"url": FIXED["url"], "status": "fixed-upstream", "note": FIXED["note"], "fixed_in": []}
    ]
    assert double["upstream"] == [REPORTED["url"]]
    assert double["upstream_issues"][0]["status"] == "reported"
    json.dumps(subject)


def test_a_fixed_upstream_note_stays_on_one_line(monkeypatch):
    monkeypatch.setattr(report, "known_issues", lambda: [{**FIXED, "note": "a\nFake line"}])
    text = doctor.render_text(doctor.Found("uv.lock", {}), doctor.summarize(result_doc()), "x")
    assert "Fixed upstream: a Fake line" in text
    assert "\nFake line" not in text


KNOWN_WITH_FIX = """
[[issue]]
subject = "openinference"
scenarios = ["anthropic/stream"]
check = "span.ended"
status = "fixed-upstream"
url = "https://example.com/issues/1"
note = "Fixed in openinference-instrumentation-anthropic 3.1.0."
fixed_in = ["openinference-instrumentation-anthropic>=3.1.0"]

[[issue]]
subject = "openinference"
scenarios = ["anthropic/basic"]
check = "tokens.single-report"
status = "reported"
url = "https://example.com/issues/2"
"""


@pytest.fixture
def known_fix(monkeypatch, tmp_path):
    path = tmp_path / "known_issues.toml"
    path.write_text(KNOWN_WITH_FIX)
    rows = report.known_issues(path)
    monkeypatch.setattr(report, "known_issues", lambda: rows)


@pytest.mark.parametrize(
    ("source", "command"),
    [
        ("uv.lock", "uv add 'openinference-instrumentation-anthropic>=3.1.0'"),
        ("poetry.lock", "poetry add 'openinference-instrumentation-anthropic>=3.1.0'"),
        ("pdm.lock", "pdm add 'openinference-instrumentation-anthropic>=3.1.0'"),
        ("Pipfile.lock", "pipenv install 'openinference-instrumentation-anthropic>=3.1.0'"),
        # doctor reads the pin from the file, so the file is what must change. A `pip install`
        # would be undone by the next `pip install -r`, and the next doctor run.
        (
            "requirements.txt",
            "require 'openinference-instrumentation-anthropic>=3.1.0' in requirements.txt",
        ),
        (
            "requirements*.txt",
            "require 'openinference-instrumentation-anthropic>=3.1.0' in your requirements files",
        ),
        (
            "pyproject.toml",
            "require 'openinference-instrumentation-anthropic>=3.1.0' in pyproject.toml",
        ),
        (".venv", "pip install 'openinference-instrumentation-anthropic>=3.1.0'"),
    ],
)
def test_a_fixed_finding_prints_the_upgrade_for_the_package_manager(known_fix, source, command):
    found = doctor.Found(source, {})
    text = doctor.render_text(found, doctor.summarize(result_doc(), source), "x")
    assert f"    Fix first: {command}\n" in text
    # Before the explanation of the class, and the workaround stays.
    ghost = text.index("ST01 Ghost span")
    assert ghost < text.index("Fix first:") < text.index("The app made the call")
    assert "Today:" in text[ghost:]


def test_a_finding_with_no_fixed_in_prints_no_upgrade(known):
    text = doctor.render_text(
        doctor.Found("uv.lock", {}), doctor.summarize(result_doc(), "uv.lock"), "x"
    )
    assert "Fix first" not in text


def test_a_reported_entry_with_fixed_in_gives_no_upgrade(monkeypatch):
    entry = {**REPORTED, "fixed_in": ["openinference-instrumentation-anthropic>=9"]}
    monkeypatch.setattr(report, "known_issues", lambda: [entry])
    (subject,) = doctor.summarize(result_doc(), "uv.lock")
    assert all(c["upgrade"] is None for c in subject["classes"])


def test_upgrade_keeps_the_highest_bound_and_skips_what_is_installed():
    specs = ["a>=1.2.0", "a>=1.10.0", "b>=2.0", "c<3"]
    assert doctor.upgrade_command("uv.lock", specs, {"b": "2.1.0"}) == "uv add 'a>=1.10.0' 'c<3'"
    assert doctor.upgrade_command("uv.lock", ["b>=2.0"], {"B": "2.0"}) is None


def test_json_carries_the_upgrade_command_and_fixed_in(known_fix):
    (subject,) = doctor.summarize(result_doc(), "poetry.lock")
    by_id = {c["id"]: c for c in subject["classes"]}
    assert by_id["ST01"]["upgrade"] == (
        "poetry add 'openinference-instrumentation-anthropic>=3.1.0'"
    )
    assert by_id["ST01"]["upstream_issues"][0]["fixed_in"] == [
        "openinference-instrumentation-anthropic>=3.1.0"
    ]
    assert by_id["ST02"]["upgrade"] is None
    assert by_id["ST02"]["upstream_issues"][0]["fixed_in"] == []


def test_findings_come_largest_cost_first():
    doc = result_doc()
    cells = doc["subjects"]["openinference"]["cells"]
    # The double span doubles the cost. A phantom count of 10,000 output tokens for 10 costs more.
    phantom = {
        "id": "output_tokens",
        "verdict": "wrong",
        "expected": 10,
        "got": 10_000,
        "where": None,
        "detail": "",
    }
    cells["anthropic/basic"]["checks"].append(phantom)
    del cells["anthropic/stream"]
    classes = doctor.summarize(doc)[0]["classes"]
    assert [c["id"] for c in classes] == ["ST04", "ST02"]
    assert classes[0]["largest_cost_error"] > classes[1]["largest_cost_error"] == 100


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


def test_requirements_with_hashes_keep_their_pins(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text(
        "anthropic==1.13.0 \\\n"
        "    --hash=sha256:157bd74dbf6a595cf9e5fcac557c791fd297924dc48261f57470c2ed6f4e6671 \\\n"
        "    --hash=sha256:ad11d9bb9adafdfea26113943bcde9973e2a439ebeeda6c89ff3e3d85bb2f5c1\n"
        "    # via -r requirements.in\n"
        "opentelemetry-sdk==1.45.2 \\\n"
        "    --hash=sha256:aaaa\n"
    )
    assert doctor._requirements([req]) == {"anthropic": "1.13.0", "opentelemetry-sdk": "1.45.2"}


def test_a_setup_that_raised_says_why_and_names_the_versions_spantruth_added():
    # A project on an older OpenTelemetry pins the SDK, and the subject's other instrumentor comes
    # at spantruth's pin, which needs a newer one. The text must say so, not only a check ID.
    doc = result_doc()
    subject = doc["subjects"]["openinference"]
    subject["versions"] = {"anthropic": "2.5.0", "openinference-instrumentation-openai": "0.1.69"}
    subject["cells"]["anthropic/basic"] = {
        "verdict": "inconclusive",
        "checks": [
            {
                "id": "subject.instrument",
                "verdict": "inconclusive",
                "expected": None,
                "got": None,
                "where": None,
                "detail": "Traceback:\n  File x\nModuleNotFoundError: No module named 'a.b'\n",
            }
        ],
    }
    found = doctor.Found("requirements.txt", {"anthropic": "2.5.0"})
    text = doctor.render_text(found, doctor.summarize(doc), "x")
    assert "Could not run 1 of 2 scenarios: ModuleNotFoundError: No module named 'a.b'" in text
    assert "spantruth's own versions: openinference-instrumentation-openai 0.1.69" in text


def test_a_failed_environment_build_says_where_the_reason_is():
    doc = result_doc()
    doc["subjects"]["openinference"]["error"] = "environment build failed: Command [uv] returned 1"
    text = doctor.render_text(doctor.Found("uv.lock", {}), doctor.summarize(doc), "x")
    assert "Python 3.12" in text and "uv printed" in text


def test_a_missing_uv_is_one_line_with_the_install_link_and_exit_3(tmp_path, capsys, monkeypatch):
    project(tmp_path, uv__lock=UV_LOCK)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    assert cli.main(["doctor", str(tmp_path)]) == 3
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert err.count("\n") == 1
    assert "needs uv" in err and "https://docs.astral.sh/uv/getting-started/installation/" in err


def test_doctor_reads_a_subdirectory(tmp_path, monkeypatch):
    sub = tmp_path / "services" / "api"
    sub.mkdir(parents=True)
    project(sub, uv__lock=UV_LOCK)
    monkeypatch.chdir(tmp_path)
    assert doctor.read_versions("services/api").source == "uv.lock"


def test_doctor_takes_the_path_of_a_lock_file(tmp_path):
    project(tmp_path, uv__lock=UV_LOCK, requirements__txt="openai==1.0\n")
    assert doctor.read_versions(tmp_path / "uv.lock").source == "uv.lock"
    found = doctor.read_versions(tmp_path / "requirements.txt")
    assert (found.source, found.versions) == ("requirements.txt", {"openai": "1.0"})


def test_doctor_says_when_the_path_does_not_exist(tmp_path):
    with pytest.raises(doctor.DoctorError, match="no such file or directory"):
        doctor.read_versions(tmp_path / "nope")


def monorepo(root):
    for sub, name in (("services/api", "uv.lock"), ("services/worker", "poetry.lock")):
        (root / sub).mkdir(parents=True)
        (root / sub / name).write_text(UV_LOCK)
    # Lock files of installed packages and of other tools are not subprojects.
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "node_modules" / "x" / "uv.lock").write_text(UV_LOCK)
    (root / ".venv" / "src").mkdir(parents=True)
    (root / ".venv" / "src" / "uv.lock").write_text(UV_LOCK)
    (root / "pyproject.toml").write_text('[tool.uv.workspace]\nmembers = ["services/*"]\n')
    return root


def test_a_monorepo_root_names_each_subproject_and_how_to_run_it(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(monorepo(tmp_path))
    assert cli.main(["doctor"]) == 2
    err = capsys.readouterr().err
    assert "spantruth doctor services/api" in err
    assert "spantruth doctor services/worker" in err
    assert "node_modules" not in err and ".venv" not in err


def test_a_monorepo_command_starts_from_the_path_given(tmp_path):
    with pytest.raises(doctor.DoctorError) as exc:
        doctor.read_versions(monorepo(tmp_path / "repo"))
    assert f"spantruth doctor {tmp_path / 'repo' / 'services' / 'api'}" in str(exc.value)


def test_a_subproject_name_cannot_send_a_terminal_escape(tmp_path):
    root = tmp_path / "repo\x1b]0;root\x07"
    sub = root / "evil\x1b[2J\nspantruth doctor /fake"
    sub.mkdir(parents=True)
    (sub / "uv.lock").write_text(UV_LOCK)
    with pytest.raises(doctor.DoctorError) as exc:
        doctor.read_versions(root)
    message = str(exc.value)
    assert "\x1b" not in message and "\x07" not in message
    assert "\nspantruth doctor /fake" not in message
    assert message.count("\n") == 1


def test_a_forged_line_in_a_subproject_name_stays_on_its_line(tmp_path):
    sub = tmp_path / "evil\x1b[31mred\n  spantruth doctor /etc"
    sub.mkdir(parents=True)
    (sub / "uv.lock").write_text(UV_LOCK)
    with pytest.raises(doctor.DoctorError) as exc:
        doctor.read_versions(tmp_path)
    message = str(exc.value)
    assert "\x1b" not in message
    assert "\n  spantruth doctor /etc" not in message
    assert "1 subproject has one" in message


def test_a_single_project_with_a_lock_file_in_its_fixtures_is_read_as_before(tmp_path):
    fixture = tmp_path / "tests" / "fixtures" / "proj"
    fixture.mkdir(parents=True)
    (fixture / "uv.lock").write_text(UV_LOCK)
    pyproject = '[project]\nname = "x"\ndependencies = ["openlit==1.45.0"]\n'
    project(tmp_path, pyproject__toml=pyproject)
    assert doctor.read_versions(tmp_path).source == "pyproject.toml"


def test_a_project_with_no_covered_library_and_a_fixture_lock_is_still_one_project(tmp_path):
    fixture = tmp_path / "tests" / "fixtures" / "x"
    fixture.mkdir(parents=True)
    (fixture / "uv.lock").write_text(UV_LOCK)
    project(tmp_path, pyproject__toml='[project]\nname = "x"\ndependencies = ["rich"]\n')
    assert doctor.read_versions(tmp_path).source == "pyproject.toml"


def test_a_requirements_file_name_cannot_send_a_terminal_escape(tmp_path):
    project(tmp_path, **{"requirements-\x1b[31mRED\nNEWLINE.txt": "openai==1.0\n"})
    found = doctor.read_versions(tmp_path)
    assert "\x1b" not in found.source and "\n" not in found.source
    text = doctor.render_text(found, doctor.summarize(result_doc()), "x")
    assert "\x1b" not in text and "\nNEWLINE" not in text


def test_a_uv_workspace_root_names_its_members_even_with_dependencies(tmp_path):
    monorepo(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["openlit"]\n'
        '[tool.uv.workspace]\nmembers = ["s/*"]\n'
    )
    with pytest.raises(doctor.DoctorError, match="subprojects have one"):
        doctor.read_versions(tmp_path)


def test_doctor_reads_only_requirements_files_by_name(tmp_path):
    (tmp_path / "notes.txt").write_text("openai==1.0\n")
    with pytest.raises(doctor.DoctorError, match=r"cannot read versions from notes\.txt"):
        doctor.read_versions(tmp_path / "notes.txt")


def test_a_root_with_requirements_is_read_and_not_a_monorepo(tmp_path):
    monorepo(tmp_path)
    project(tmp_path, requirements__txt="openai==1.0\n")
    assert doctor.read_versions(tmp_path).source == "requirements.txt"


TABLE = {
    "langchain": {"title": "LangChain", "issue": "https://example.com/issues/77"},
    "langchain-openai": {"title": "LangChain", "issue": "https://example.com/issues/77"},
    "cohere": {"title": "Cohere", "issue": "https://example.com/issues/83"},
    "openlit": {"title": "OpenLIT", "issue": "https://example.com/issues/3"},
}


@pytest.fixture
def table(monkeypatch):
    monkeypatch.setattr(doctor, "uncovered_table", lambda: TABLE)


def test_doctor_names_each_library_no_subject_covers_once(table):
    versions = {"langchain": "1.0", "langchain-openai": "1.0", "cohere": "5.0", "rich": "14"}
    assert doctor.uncovered(versions) == [
        {"title": "Cohere", "packages": ["cohere"], "issue": "https://example.com/issues/83"},
        {
            "title": "LangChain",
            "packages": ["langchain", "langchain-openai"],
            "issue": "https://example.com/issues/77",
        },
    ]


def test_a_package_that_a_subject_detects_is_covered(table):
    assert doctor.uncovered({"openlit": "1.45.0"}) == []


def test_every_uncovered_entry_names_a_title_and_an_issue_to_ask_in():
    rows = doctor.uncovered_table()
    assert {"litellm", "langchain", "instructor"} <= set(rows)
    # Gemini is a provider now, so a google-genai project is graded, not sent to an issue.
    assert not {"google-genai", "google-generativeai"} & set(rows)
    for name, row in rows.items():
        assert name == subjects.package_name(name), name
        assert row["title"], name
        assert row["issue"].startswith("https://github.com/Plexito-de/spantruth/issues/"), name


def test_text_names_the_uncovered_libraries_and_the_wrapper_guide(table):
    found = doctor.Found("uv.lock", {"anthropic": "2.5.0", "cohere": "5.0"})
    text = doctor.render_text(found, doctor.summarize(result_doc()), "x")
    assert "Not covered yet: Cohere (cohere). Ask for it at https://example.com/issues/83" in text
    assert "docs/test-your-wrapper.md" in text


def test_text_has_no_uncovered_section_when_everything_is_covered(table):
    text = doctor.render_text(doctor.Found("uv.lock", {}), doctor.summarize(result_doc()), "x")
    assert "Not covered yet" not in text


def test_json_carries_the_uncovered_libraries(table):
    found = doctor.Found("uv.lock", {"cohere": "5.0"})
    doc = doctor.json_doc(found, doctor.summarize(result_doc()), "x", 1)
    assert doc["uncovered"][0]["title"] == "Cohere"
    assert doc["exit_code"] == 1


def test_only_uncovered_libraries_still_exit_2_and_say_where_to_ask(tmp_path, capsys, table):
    project(tmp_path, requirements__txt="langchain==1.0\nrich==14.0\n")
    assert cli.main(["doctor", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "Not covered yet: LangChain (langchain)" in err
    assert "https://example.com/issues/77" in err
    assert "test-your-wrapper.md" in err


def test_json_with_nothing_to_check_still_prints_the_uncovered_libraries(tmp_path, capsys, table):
    project(tmp_path, requirements__txt="cohere==5.0\n")
    assert cli.main(["doctor", str(tmp_path), "--json"]) == 2
    doc = json.loads(capsys.readouterr().out)
    assert doc["exit_code"] == 2
    assert doc["subjects"] == []
    assert [u["title"] for u in doc["uncovered"]] == ["Cohere"]


# `doctor --base`: two commits with different lock files.

OLD_PIN = "openinference-instrumentation-anthropic==3.0.0"


def fake_run(calls):
    """A runner that loses the stream span at the old pin and passes at any other, and counts."""

    def run(selected, ids, cache, latest=False, jobs=4, overrides=()):
        calls.append(overrides)
        doc = result_doc()
        subject = doc["subjects"]["openinference"]
        subject["versions"] = dict(spec.split("==") for spec in overrides)
        if OLD_PIN not in overrides:
            subject["cells"] = {sid: {"verdict": "pass", "checks": []} for sid in subject["cells"]}
        else:
            subject["cells"]["anthropic/basic"] = {"verdict": "pass", "checks": []}
        return doc

    return run


def git(repo, *args):
    import subprocess

    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def two_commits(repo, old, new, name="requirements.txt"):
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q", "-b", "main")
    for text, message in ((old, "old"), (new, "new")):
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
        git(repo, "add", name)
        git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", message)
    return repo


def pins(version):
    return f"anthropic==2.5.0\nopeninference-instrumentation-anthropic=={version}\n"


@pytest.fixture
def runs(monkeypatch):
    calls = []
    monkeypatch.setattr(doctor.runner, "run", fake_run(calls))
    monkeypatch.setattr(report, "known_issues", lambda: [])
    return calls


def test_base_names_a_finding_that_the_bump_fixes(tmp_path, capsys, runs):
    repo = two_commits(tmp_path / "app", pins("3.0.0"), pins("3.1.0"))
    out, body = tmp_path / "r.json", tmp_path / "comment.md"
    code = cli.main(
        ["doctor", str(repo), "--base", "HEAD~1", "--out", str(out), "--comment", str(body)]
    )
    text = capsys.readouterr().out
    assert code == 0
    assert "Compared with HEAD~1" in text
    assert "openinference-instrumentation-anthropic 3.0.0 -> 3.1.0" in text
    assert "Fixed in openinference: ST01 Ghost span" in text
    assert len(runs) == 2
    assert (tmp_path / "r-base.json").is_file()
    comment = body.read_text()
    assert comment.startswith(report.comment_marker(f"doctor-{repo}"))
    assert "1 finding fixed, 0 new" in comment
    assert "| `openinference` | [ST01 Ghost span]" in comment


def test_base_fails_on_a_finding_that_the_change_adds(tmp_path, capsys, runs):
    repo = two_commits(tmp_path / "app", pins("3.1.0"), pins("3.0.0"))
    code = cli.main(["doctor", str(repo), "--base", "HEAD~1", "--out", str(tmp_path / "r.json")])
    assert code == 1
    assert "New in openinference: ST01 Ghost span" in capsys.readouterr().out


def test_base_runs_once_when_the_versions_doctor_uses_did_not_change(tmp_path, capsys, runs):
    old = pins("3.1.0") + "rich==13.0\n"
    repo = two_commits(tmp_path / "app", old, pins("3.1.0") + "rich==14.0\n")
    assert (
        cli.main(["doctor", str(repo), "--base", "HEAD~1", "--out", str(tmp_path / "r.json")]) == 0
    )
    assert len(runs) == 1
    assert "No finding changed" in capsys.readouterr().out


def test_base_reads_a_subproject_and_its_lock_file(tmp_path, capsys, runs):
    repo = tmp_path / "mono"
    sub = repo / "services" / "api"
    sub.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    for version in ("3.0.0", "3.1.0"):
        (sub / "requirements.txt").write_text(pins(version))
        git(repo, "add", ".")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", version)
    args = ["doctor", str(sub), "--base", "HEAD~1", "--out", str(tmp_path / "r.json"), "--json"]
    assert cli.main(args) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["base"]["ref"] == "HEAD~1"
    assert doc["changes"] == [
        {
            "subject": "openinference",
            "class": "ST01",
            "name": "Ghost span",
            "change": "fixed",
            "base_scenarios": 1,
            "head_scenarios": 0,
        }
    ]


def test_base_with_an_unknown_ref_says_how_to_fetch_it(tmp_path, capsys, runs):
    repo = two_commits(tmp_path / "app", pins("3.0.0"), pins("3.1.0"))
    assert cli.main(["doctor", str(repo), "--base", "nope"]) == 2
    assert "git fetch" in capsys.readouterr().err


def test_base_with_no_dependency_file_at_the_ref_counts_every_finding_as_new(
    tmp_path, capsys, runs
):
    repo = two_commits(tmp_path / "app", "", pins("3.0.0"))
    git(repo, "rm", "-q", "--cached", "requirements.txt")
    (repo / "README").write_text("x")
    git(repo, "add", "README")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "x")
    git(repo, "add", "requirements.txt")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "y")
    args = ["doctor", str(repo), "--base", "HEAD~1", "--out", str(tmp_path / "r.json")]
    assert cli.main(args) == 1
    assert "New in openinference: ST01 Ghost span" in capsys.readouterr().out


def test_an_incomplete_run_is_not_compared(tmp_path, capsys, monkeypatch, runs):
    real = doctor.runner.run

    def broken(*args, **kwargs):
        doc = real(*args, **kwargs)
        doc["subjects"]["openinference"]["error"] = "environment build failed"
        return doc

    monkeypatch.setattr(doctor.runner, "run", broken)
    repo = two_commits(tmp_path / "app", pins("3.0.0"), pins("3.1.0"))
    body = tmp_path / "c.md"
    args = ["doctor", str(repo), "--base", "HEAD~1", "--out", str(tmp_path / "r.json")]
    assert cli.main([*args, "--comment", str(body)]) == 3
    assert "not compared" in capsys.readouterr().out
    assert "could not run every library" in body.read_text()


def test_the_comment_keeps_values_from_the_lock_file_in_code_spans():
    versions = [("pkg", "1.0", "2.0`<script>")]
    body = doctor_base.comment([], "main", versions, [], False, "")
    assert "`` 2.0`<script> ``" in body
    assert body.count("<!--") == 1


def test_a_newline_in_a_repository_value_cannot_leave_its_code_span():
    evil = "2.0\n\n![x](https://evil.example/x.png) @someone"
    body = doctor_base.comment(
        [], f"main\n\n# {evil}", [(f"pkg\n\n{evil}", "1.0", evil)], [], False, ""
    )
    assert "\n![x]" not in body and "\n# " not in body
    assert "\n@someone" not in body


def test_each_project_path_gets_its_own_comment_marker():
    root = doctor_base.comment([], None, [], [], False, "", project=".")
    api = doctor_base.comment([], None, [], [], False, "", project="services/api")
    worker = doctor_base.comment([], None, [], [], False, "", project="./services/worker/")
    assert root.startswith(report.comment_marker("doctor"))
    markers = {body.splitlines()[0] for body in (root, api, worker)}
    assert len(markers) == 3
    assert "`services/api`" in api


def test_base_passes_when_the_same_finding_is_on_both_sides(tmp_path, capsys, runs):
    # The bump moves a version doctor runs, so both sides run, and both lose the stream span.
    old = pins("3.0.0").replace("anthropic==2.5.0", "anthropic==2.4.0")
    repo = two_commits(tmp_path / "app", old, pins("3.0.0"))
    args = ["doctor", str(repo), "--base", "HEAD~1", "--out", str(tmp_path / "r.json")]
    assert cli.main(args) == 0
    assert len(runs) == 2
    assert "No finding changed" in capsys.readouterr().out


def test_base_reads_a_file_under_a_requirements_directory(tmp_path, capsys, runs):
    repo = two_commits(tmp_path / "app", pins("3.0.0"), pins("3.1.0"), name="requirements/dev.txt")
    target = repo / "requirements" / "dev.txt"
    args = ["doctor", str(target), "--base", "HEAD~1", "--out", str(tmp_path / "r.json")]
    assert cli.main(args) == 0
    assert "Fixed in openinference: ST01 Ghost span" in capsys.readouterr().out


def test_a_provider_counts_when_the_project_has_its_sdk_package(monkeypatch):
    fake = {
        "openai/basic": SimpleNamespace(provider="openai"),
        "gemini/basic": SimpleNamespace(provider="gemini"),
        "azure/basic": SimpleNamespace(provider="azure"),
    }
    monkeypatch.setattr(doctor.scenarios, "SCENARIOS", fake)
    monkeypatch.setattr(doctor.scenarios, "get", fake.__getitem__)
    assert doctor.scenario_ids({"google-genai": "1.0"}) == ["gemini/basic"]
    assert doctor.scenario_ids({"openai": "3.0"}) == ["openai/basic", "azure/basic"]
