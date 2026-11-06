"""The nightly watcher: what moved between two runs, as a history, a feed and a page."""

import json
from xml.etree import ElementTree

import pytest

from spantruth import cli, watch

ATOM = "{http://www.w3.org/2005/Atom}"


def cell(verdict, check="span.ended", detail=""):
    return {
        "verdict": verdict,
        "checks": [
            {"id": check, "verdict": verdict, "expected": None, "got": None, "detail": detail}
        ],
    }


def document(created, cells, versions=None, title="OpenInference", kind="library"):
    return {
        "schema": 1,
        "spantruth": "0.4.0",
        "created": created,
        "mode": "latest",
        "scenarios": {sid: {"title": sid, "provider": "anthropic"} for sid in cells},
        "subjects": {
            "openinference": {
                "title": title,
                "homepage": "https://example.com",
                "kind": kind,
                "versions": versions or {"openinference-instrumentation-anthropic": "3.3.1"},
                "cells": cells,
            }
        },
    }


OLD = document("2026-11-05T03:00:00+00:00", {"a/x": cell("lost"), "a/y": cell("pass")})


def test_a_new_release_that_fixes_a_cell_is_a_change():
    new = document(
        "2026-11-06T03:00:00+00:00",
        {"a/x": cell("pass"), "a/y": cell("pass")},
        {"openinference-instrumentation-anthropic": "3.3.2"},
    )
    (change,) = watch.changes(OLD, new)
    assert change["subject"] == "openinference"
    assert change["versions"] == {"openinference-instrumentation-anthropic": ["3.3.1", "3.3.2"]}
    (fixed,) = change["fixed"]
    assert fixed["scenario"] == "a/x"
    assert fixed["was"] == "lost"
    assert fixed["bug"] == "ST01 Ghost span"
    assert change["broken"] == []
    assert "3.3.2" in change["title"] and "fixes 1" in change["title"]


def test_a_new_release_that_breaks_a_cell_is_a_change():
    new = document(
        "2026-11-06T03:00:00+00:00",
        {"a/x": cell("lost"), "a/y": cell("wrong", "output_tokens")},
        {"openinference-instrumentation-anthropic": "3.3.2"},
    )
    (change,) = watch.changes(OLD, new)
    (broken,) = change["broken"]
    assert broken["scenario"] == "a/y"
    assert broken["now"] == "wrong"


def test_a_cell_that_moved_with_no_new_version_is_not_news_about_the_library():
    new = document("2026-11-06T03:00:00+00:00", {"a/x": cell("pass"), "a/y": cell("pass")})
    assert watch.changes(OLD, new) == []


def test_a_new_version_that_moves_no_cell_is_not_news():
    new = document(
        "2026-11-06T03:00:00+00:00",
        {"a/x": cell("lost"), "a/y": cell("pass")},
        {"openinference-instrumentation-anthropic": "3.3.2"},
    )
    assert watch.changes(OLD, new) == []


def test_a_new_scenario_is_not_a_change():
    new = document(
        "2026-11-06T03:00:00+00:00",
        {"a/x": cell("lost"), "a/y": cell("pass"), "a/z": cell("wrong")},
        {"openinference-instrumentation-anthropic": "3.3.2"},
    )
    assert watch.changes(OLD, new) == []


def test_a_cell_the_harness_could_not_run_is_not_a_change():
    new = document(
        "2026-11-06T03:00:00+00:00",
        {"a/x": cell("inconclusive", "harness"), "a/y": cell("pass")},
        {"openinference-instrumentation-anthropic": "3.3.2"},
    )
    assert watch.changes(OLD, new) == []
    assert watch.changes(new, OLD) == []


def test_controls_are_not_news():
    old = document("2026-11-05T03:00:00+00:00", {"a/x": cell("lost")}, kind="control")
    new = document(
        "2026-11-06T03:00:00+00:00",
        {"a/x": cell("pass")},
        {"openinference-instrumentation-anthropic": "9"},
        kind="control",
    )
    assert watch.changes(old, new) == []


def test_history_has_one_line_per_subject_with_counts_and_classes():
    (line,) = watch.history_lines(OLD)
    assert line["date"] == "2026-11-05"
    assert line["subject"] == "openinference"
    assert line["cells"] == {"lost": 1, "pass": 1}
    assert line["classes"] == {"ST01": 1}
    assert line["versions"]


def test_merging_history_replaces_the_line_of_the_same_day_and_keeps_the_rest():
    earlier = {"date": "2026-11-04", "subject": "openinference", "cells": {}}
    same = {"date": "2026-11-05", "subject": "openinference", "cells": {"pass": 9}}
    (kept, line) = watch.merge_history([earlier, same], watch.history_lines(OLD))
    assert kept == earlier
    assert line["cells"] == {"lost": 1, "pass": 1}


def change_with(title):
    return {
        "id": "tag:plexito-de.github.io,2026:spantruth/x",
        "created": "2026-11-06T03:00:00+00:00",
        "subject": "s",
        "title": title,
        "versions": {},
        "fixed": [
            {
                "scenario": "a/x",
                "check": "span.ended",
                "bug": "ST01 Ghost span",
                "was": "lost",
                "now": "pass",
                "issue": "https://example.com/1",
            }
        ],
        "broken": [],
    }


def test_feed_is_valid_atom_and_escapes_library_values():
    xml = watch.feed([change_with('<script>&"x')])
    root = ElementTree.fromstring(xml)
    assert root.tag == f"{ATOM}feed"
    (entry,) = root.findall(f"{ATOM}entry")
    assert entry.find(f"{ATOM}title").text == '<script>&"x'
    assert "<script>" not in xml


def test_empty_feed_is_valid_and_has_an_updated_date():
    root = ElementTree.fromstring(watch.feed([]))
    assert root.find(f"{ATOM}updated").text
    assert root.findall(f"{ATOM}entry") == []


def test_page_lists_changes_escaped_newest_first():
    older = change_with("older")
    newer = {**change_with("<b>newer</b>"), "created": "2026-11-07T03:00:00+00:00"}
    page = watch.page([older, newer])
    assert "&lt;b&gt;newer" in page
    assert "<b>newer" not in page
    assert page.index("newer") < page.index("older")
    assert "feed.xml" in page


def test_a_script_address_in_an_issue_is_never_a_link():
    bad = change_with("t")
    bad["fixed"][0]["issue"] = "javascript:alert(1)"
    assert "javascript:" not in watch.page([bad])


def write_state(directory, latest=None, history=(), changes=()):
    directory.mkdir(parents=True, exist_ok=True)
    if latest is not None:
        (directory / "latest.json").write_text(json.dumps(latest))
    (directory / "history.jsonl").write_text("".join(json.dumps(h) + "\n" for h in history))
    (directory / "changes.jsonl").write_text("".join(json.dumps(c) + "\n" for c in changes))


def test_the_command_writes_every_file_and_keeps_earlier_state(tmp_path):
    state, out = tmp_path / "state", tmp_path / "site"
    earlier = {"date": "2026-11-01", "subject": "openinference", "cells": {}}
    write_state(state, OLD, [earlier], [change_with("older")])
    current = tmp_path / "latest.json"
    current.write_text(
        json.dumps(
            document(
                "2026-11-06T03:00:00+00:00",
                {"a/x": cell("pass"), "a/y": cell("pass")},
                {"openinference-instrumentation-anthropic": "3.3.2"},
            )
        )
    )
    assert cli.main(["watch", str(current), "--state", str(state), "--out", str(out)]) == 0
    assert len((out / "history.jsonl").read_text().splitlines()) == 2
    assert len((out / "changes.jsonl").read_text().splitlines()) == 2
    assert ElementTree.fromstring((out / "feed.xml").read_text()).tag == f"{ATOM}feed"
    assert "older" in (out / "changes.html").read_text()


def test_the_first_run_has_no_state_and_no_change(tmp_path):
    current = tmp_path / "latest.json"
    current.write_text(json.dumps(OLD))
    out = tmp_path / "site"
    assert (
        cli.main(["watch", str(current), "--state", str(tmp_path / "none"), "--out", str(out)]) == 0
    )
    assert (out / "changes.jsonl").read_text() == ""
    assert len((out / "history.jsonl").read_text().splitlines()) == 1


def test_a_corrupt_history_stops_the_run_and_loses_nothing(tmp_path):
    state, out = tmp_path / "state", tmp_path / "site"
    write_state(state, OLD)
    (state / "history.jsonl").write_text("{not json\n")
    current = tmp_path / "latest.json"
    current.write_text(json.dumps(OLD))
    assert cli.main(["watch", str(current), "--state", str(state), "--out", str(out)]) == 3
    assert not (out / "history.jsonl").exists()


@pytest.mark.parametrize("verdict", ["pass", "n/a"])
def test_a_cell_that_was_not_failing_is_not_fixed(verdict):
    old = document("2026-11-05T03:00:00+00:00", {"a/x": cell(verdict)})
    new = document(
        "2026-11-06T03:00:00+00:00",
        {"a/x": cell("pass")},
        {"openinference-instrumentation-anthropic": "3.3.2"},
    )
    assert watch.changes(old, new) == []
