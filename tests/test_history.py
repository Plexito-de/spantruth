"""The version table: recent releases, a line per version, and the version that fixed a check."""

import json
from pathlib import Path

import pytest
import yaml

from spantruth import cli, doctor, history, scenarios, subjects

REPO = Path(__file__).resolve().parents[1]


def pypi(*releases):
    """A PyPI JSON document with these (version, date, yanked) releases."""
    return {
        "releases": {
            v: [{"upload_time_iso_8601": f"{d}T10:00:00.000Z", "yanked": y}] for v, d, y in releases
        }
    }


def line(version, failing=None, unknown=(), error=""):
    return {
        "version": version,
        "released": "",
        "run": "",
        "failing": failing or {},
        "partial": {},
        "unknown": list(unknown),
        "error": error,
    }


def test_versions_sort_by_release_not_by_text():
    ordered = ["0.9", "1.0.dev1", "1.0a1", "1.0b2", "1.0rc1", "1.0", "1.0.post1", "1.2", "1.10"]
    assert sorted(ordered, key=history.version_key) == ordered
    assert history.version_key("1.0") == history.version_key("1.0.0")
    assert history.version_key("not a version") is None


def test_recent_takes_the_last_final_releases_oldest_first_and_skips_yanked_ones():
    doc = pypi(
        ("1.9.0", "2026-01-01", False),
        ("1.10.0", "2026-02-01", False),
        ("1.11.0", "2026-03-01", True),
        ("1.12.0b1", "2026-04-01", False),
        ("1.8.0", "2025-12-01", False),
    )
    doc["releases"]["1.13.0"] = []
    assert history.recent(doc, 2) == [("1.9.0", "2026-01-01"), ("1.10.0", "2026-02-01")]


def test_a_package_with_only_pre_releases_uses_them():
    doc = pypi(("1.2b0", "2026-01-01", False), ("1.3b0", "2026-02-01", False))
    assert [v for v, _ in history.recent(doc, 5)] == ["1.2b0", "1.3b0"]


def test_a_package_named_for_a_provider_runs_only_that_providers_scenarios():
    oi = subjects.load("openinference")
    ids = history.scenario_ids(oi, "openinference-instrumentation-openai")
    assert ids and all(i.startswith("openai/") for i in ids)
    both = history.scenario_ids(subjects.load("logfire"), "logfire")
    assert {i.split("/")[0] for i in both} == {"openai", "anthropic"}


def test_a_subject_with_no_package_of_its_own_tracks_its_sdk():
    assert history.tracked(subjects.load("anthropic-sdk")) == ["anthropic"]
    assert history.tracked(subjects.load("openllmetry")) == [
        "opentelemetry-instrumentation-openai",
        "opentelemetry-instrumentation-anthropic",
    ]


def test_an_entry_keeps_failing_checks_with_their_class_and_the_cells_it_could_not_run():
    result = {
        "cells": {
            "a/x": {
                "verdict": "lost",
                "checks": [
                    {"id": "span.ended", "verdict": "lost"},
                    {"id": "span.parented", "verdict": "pass"},
                    {"id": "model", "verdict": "pass"},
                    {"id": "cost", "verdict": "n/a"},
                ],
            },
            "a/y": {"verdict": "pass", "checks": [{"id": "span.parented", "verdict": "pass"}]},
            "a/z": {"verdict": "inconclusive", "checks": [{"id": "harness", "verdict": "x"}]},
            "a/w": {
                "verdict": "inconclusive",
                "checks": [{"id": "subject.instrument", "verdict": "inconclusive"}],
            },
        }
    }
    e = history.entry(result, "3.3.0", "2026-10-01", "2026-11-26")
    assert e["failing"] == {"a/x": {"span.ended": "ST01"}}
    assert e["unknown"] == ["a/z"]
    assert e["setup_failed"] == ["a/w"]  # the setup raised: not run, and not a failing check
    assert e["partial"] == {}
    assert e["error"] == ""


@pytest.mark.parametrize(
    ("states", "fixed"),
    [
        (["f", "f", "p", "p"], "4"),  # versions are numbered from 2
        (["f", "p", "f", "p"], "5"),
        (["f", "p", "f"], None),
        (["p", "p"], None),
        (["f", "p", "?", "p"], "3"),
        (["f", "p", "error"], "3"),
        (["f", "f", "?"], None),
        (["f", "f", "setup", "p"], "5"),  # a version whose setup raised graded nothing else
        (["f", "setup"], None),
    ],
)
def test_first_fixed_is_the_first_version_that_passes_and_keeps_passing(states, fixed):
    versions = []
    for n, s in enumerate(states, 2):
        failing = {"a/x": {"span.ended": "ST01"}} if s == "f" else {}
        v = line(
            str(n),
            failing,
            unknown=["a/x"] if s == "?" else (),
            error="build failed" if s == "error" else "",
        )
        if s == "setup":
            v["setup_failed"] = ["a/x"]
        versions.append(v)
    got = history.first_fixed(versions)
    if fixed is None:
        assert got == []
    else:
        assert got == [{"scenario": "a/x", "check": "span.ended", "bug": "ST01", "version": fixed}]


def fake_run(calls):
    def run(selected, ids, cache, jobs=1, overrides=()):
        calls.append(overrides)
        (version,) = [o.split("==")[1] for o in overrides]
        cells = {
            sid: {
                "verdict": "lost" if version == "1.0" else "pass",
                "checks": [{"id": "span.ended", "verdict": "lost" if version == "1.0" else "pass"}],
            }
            for sid in ids
        }
        return {
            "created": "2026-11-26T03:00:00+00:00",
            "subjects": {selected[0].name: {"cells": cells}},
        }

    return run


def test_build_runs_each_release_once_and_reuses_an_earlier_entry(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(history.runner, "run", fake_run(calls))
    logfire = subjects.load("logfire")
    releases = {"logfire": [("1.0", "2026-01-01"), ("1.1", "2026-02-01")]}
    first = history.build([logfire], releases, tmp_path, jobs=2)
    assert sorted(calls) == [("logfire==1.0",), ("logfire==1.1",)]
    package = first["subjects"]["logfire"]["packages"]["logfire"]
    assert package["pinned"] == "5.4.0"
    assert [v["version"] for v in package["versions"]] == ["1.0", "1.1"]
    assert {f["version"] for f in package["first_fixed"]} == {"1.1"}
    calls.clear()
    releases["logfire"].append(("1.2", "2026-03-01"))
    second = history.build([logfire], releases, tmp_path, previous=first)
    assert calls == [("logfire==1.2",)]
    assert len(second["subjects"]["logfire"]["packages"]["logfire"]["versions"]) == 3


def test_a_changed_suite_runs_every_release_again(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(history.runner, "run", fake_run(calls))
    logfire = subjects.load("logfire")
    releases = {"logfire": [("1.0", "2026-01-01")]}
    first = history.build([logfire], releases, tmp_path)
    first["subjects"]["logfire"]["packages"]["logfire"]["suite"] = "changed"
    calls.clear()
    history.build([logfire], releases, tmp_path, previous=first)
    assert calls == [("logfire==1.0",)]


def test_the_suite_ignores_the_modules_that_only_render(tmp_path, monkeypatch):
    package = tmp_path / "spantruth"
    package.mkdir()
    (package / "checks.py").write_text("A = 1\n")
    (package / "site.py").write_text("B = 1\n")
    monkeypatch.setattr(history.paths, "PACKAGE", package)
    logfire = subjects.load("logfire")
    before = history.suite(logfire)
    (package / "site.py").write_text("B = 2\n")
    assert history.suite(logfire) == before
    (package / "checks.py").write_text("A = 2\n")
    assert history.suite(logfire) != before


def test_lookup_finds_a_version_by_its_release_number():
    table = {"subjects": {"s": {"packages": {"p": {"versions": [line("1.2.0")]}}}}}
    assert history.lookup(table, "s", "p", "1.2")["version"] == "1.2.0"
    assert history.lookup(table, "s", "p", "1.3") is None
    assert history.lookup(table, "other", "p", "1.2") is None


def test_load_refuses_a_table_of_another_schema(tmp_path):
    path = tmp_path / "versions.json"
    assert history.load(path) is None
    path.write_text(json.dumps({"schema": 99}))
    with pytest.raises(history.HistoryError):
        history.load(path)


def test_the_command_caps_the_number_of_releases(capsys):
    assert cli.main(["history", "--out", "x.json", "--last", str(history.MAX_RELEASES + 1)]) == 2
    assert "--last takes" in capsys.readouterr().err


def test_the_command_reads_pypi_and_writes_the_table(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(history.runner, "run", fake_run(calls))
    monkeypatch.setattr(
        history, "fetch", lambda p: pypi(("1.0", "2026-01-01", False), ("1.1", "2026-02-01", False))
    )
    out = tmp_path / "versions.json"
    code = cli.main(
        ["history", "--subject", "logfire", "--subject", "control-honest", "--out", str(out)]
    )
    assert code == 0
    table = history.load(out)
    assert list(table["subjects"]) == ["logfire"]  # the controls are not in the table
    assert table["subjects"]["logfire"]["detect"] == ["logfire"]


def test_the_shipped_table_loads_and_names_built_in_subjects_and_scenarios():
    table = history.load()
    assert table is not None, "versions.json ships with the package; refresh-versions.sh writes it"
    assert set(table["subjects"]) <= set(subjects.all_names())
    every = set(scenarios.SCENARIOS)
    for s in table["subjects"].values():
        for p in s["packages"].values():
            assert set(p["scenarios"]) <= every
            for v in p["versions"]:
                assert set(v["failing"]) <= set(p["scenarios"])


def test_the_nightly_job_publishes_the_table_and_keeps_the_last_one():
    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "report.yml").read_text())
    steps = {s.get("name"): s for s in workflow["jobs"]["run"]["steps"]}
    assert "versions.json" in steps["Fetch the last published state"]["run"]
    nightly = steps["Version history"]
    assert "spantruth history --out site/versions.json" in nightly["run"]
    assert nightly["if"] == "github.event_name != 'push'"
    assert nightly["continue-on-error"] is True
    keep = steps["Keep the last version table"]["run"]
    assert "cp state/versions.json site/versions.json" in keep
    assert "cp versions.json site/versions.json" in keep  # the committed table, on the first run


# `doctor --fast`


def fast_table():
    return {
        "schema": 1,
        "spantruth": "0.5.0",
        "created": "2026-11-26T03:00:00+00:00",
        "releases": 5,
        "subjects": {
            "openinference": {
                "packages": {
                    "openinference-instrumentation-anthropic": {
                        "suite": history.suite(subjects.load("openinference")),
                        "scenarios": ["anthropic/basic", "anthropic/stream-early-exit"],
                        "versions": [
                            line("3.2.0", {"anthropic/stream-early-exit": {"span.ended": "ST01"}}),
                            line("3.3.0"),
                        ],
                        "first_fixed": [
                            {
                                "scenario": "anthropic/stream-early-exit",
                                "check": "span.ended",
                                "bug": "ST01",
                                "version": "3.3.0",
                            }
                        ],
                    },
                    "openinference-instrumentation-openai": {
                        "suite": history.suite(subjects.load("openinference")),
                        "scenarios": ["openai/basic"],
                        "versions": [line("0.1.69")],
                        "first_fixed": [],
                    },
                }
            }
        },
    }


IDS = ["anthropic/basic", "anthropic/stream-early-exit"]


def test_the_table_answers_a_version_it_ran_and_names_the_fix():
    oi = subjects.load("openinference")
    versions = {"openinference-instrumentation-anthropic": "3.2.0", "anthropic": "2.4.0"}
    answered, missing, notes = history.answer(fast_table(), [oi], versions, IDS)
    assert notes == []  # anthropic 2.4.0 is the pin the table ran
    assert missing == []
    (row,) = answered[0]["packages"]
    assert row["failing"] == {"anthropic/stream-early-exit": {"span.ended": "ST01"}}
    assert row["fixed_in"] == {"anthropic/stream-early-exit span.ended": "3.3.0"}
    text = history.render_answer(fast_table(), answered)
    assert "span.ended (ST01 Ghost span), fixed in 3.3.0" in text
    assert "without --fast" in text


@pytest.mark.parametrize("version", ["3.1.0", None])
def test_a_version_the_table_did_not_run_goes_to_a_local_run(version):
    oi = subjects.load("openinference")
    versions = {"openinference-instrumentation-anthropic": version}
    assert history.answer(fast_table(), [oi], versions, IDS)[:2] == ([], ["openinference"])


def test_a_subject_the_table_lacks_goes_to_a_local_run():
    logfire = subjects.load("logfire")
    got = history.answer(fast_table(), [logfire], {"logfire": "5.4.0"}, IDS)
    assert got[:2] == ([], ["logfire"])


def test_fast_runs_locally_only_what_the_table_lacks(tmp_path, monkeypatch, capsys):
    (tmp_path / "requirements.txt").write_text(
        "openinference-instrumentation-anthropic==3.2.0\nlogfire==5.4.0\nanthropic==2.4.0\n"
    )
    monkeypatch.setattr(history, "load", lambda path=None: fast_table())
    ran = {}

    def local(directory, out, cache, jobs=4, scenario=None, only=None, **kwargs):
        ran["only"] = only
        return 0

    monkeypatch.setattr(doctor, "run", local)
    code = cli.main(["doctor", "--fast", str(tmp_path)])
    out = capsys.readouterr().out
    assert ran["only"] == ["logfire"]
    assert "From the version table of 2026-11-26" in out
    assert code == 1  # the table's answer has a failing check


def test_fast_with_every_answer_in_the_table_runs_nothing(tmp_path, monkeypatch, capsys):
    (tmp_path / "requirements.txt").write_text(
        "openinference-instrumentation-anthropic==3.3.0\nanthropic==2.4.0\n"
    )
    monkeypatch.setattr(history, "load", lambda path=None: fast_table())
    monkeypatch.setattr(doctor, "run", lambda *a, **k: pytest.fail("ran locally"))
    assert cli.main(["doctor", "--fast", str(tmp_path)]) == 0
    assert "No problem in 2 scenarios" in capsys.readouterr().out


def mlflow_versions():
    """The shape the first table run had: the setup raised on every release before 3.18.0."""
    setup = {
        "cells": {"a/x": {"checks": [{"id": "subject.instrument", "verdict": "inconclusive"}]}}
    }
    graded = {
        "cells": {
            "a/x": {
                "checks": [
                    {"id": "span.parented", "verdict": "pass"},
                    {"id": "finish_reasons", "verdict": "missing"},
                ]
            }
        }
    }
    return [history.entry(setup, v, "", "") for v in ("3.16.1", "3.17.0", "3.17.1", "3.17.2")] + [
        history.entry(graded, "3.18.0", "", "")
    ]


def test_a_setup_that_raises_is_not_a_failing_check_and_never_a_fix():
    versions = mlflow_versions()
    assert [v["failing"] for v in versions[:4]] == [{}] * 4
    assert versions[0]["setup_failed"] == ["a/x"]
    assert history.first_fixed(versions) == []


def test_fast_says_when_spantruths_setup_does_not_run_on_a_version():
    table = fast_table()
    package = table["subjects"]["openinference"]["packages"][
        "openinference-instrumentation-anthropic"
    ]
    package["versions"][0]["setup_failed"] = ["anthropic/basic"]
    oi = subjects.load("openinference")
    versions = {"openinference-instrumentation-anthropic": "3.2.0"}
    answered, _, _ = history.answer(table, [oi], versions, IDS)
    assert answered[0]["packages"][0]["setup_failed"] == 1
    text = history.render_answer(table, answered)
    assert "spantruth's setup does not run on this version: 1 of 2 scenarios" in text


def test_an_older_table_that_lists_a_setup_failure_as_failing_reads_the_same():
    hit = {"failing": {"a/x": {"subject.instrument": ""}, "a/y": {"span.ended": "ST01"}}}
    assert history.setup_failed(hit) == {"a/x"}


def test_a_table_made_by_other_grading_goes_to_a_local_run_and_says_so():
    table = fast_table()
    for p in table["subjects"]["openinference"]["packages"].values():
        p["suite"] = "older"
    oi = subjects.load("openinference")
    versions = {"openinference-instrumentation-anthropic": "3.3.0"}
    answered, missing, notes = history.answer(table, [oi], versions, IDS)
    assert (answered, missing) == ([], ["openinference"])
    assert "made by spantruth 0.5.0" in notes[0] and "runs locally" in notes[0]


def test_fast_says_when_the_projects_sdk_differs_from_the_tables():
    oi = subjects.load("openinference")
    versions = {"openinference-instrumentation-anthropic": "3.3.0", "anthropic": "2.12.0"}
    _, _, notes = history.answer(fast_table(), [oi], versions, IDS)
    assert notes == [
        "The table ran OpenInference with anthropic 2.4.0, and your project has anthropic 2.12.0."
    ]


def test_the_table_names_the_sdk_versions_each_subject_ran_with():
    assert history.sdk_pins(subjects.load("openlit")) == {
        "anthropic": "0.125.0",
        "openai": "2.54.0",
    }
    assert history.sdk_pins(subjects.load("logfire")) == {"anthropic": "2.4.0", "openai": "3.42.1"}
