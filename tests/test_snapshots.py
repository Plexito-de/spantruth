"""Dated copies of the report: written when the results change, forwarded when they do not."""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from spantruth import cli, snapshots

REPO = Path(__file__).resolve().parents[1]


def doc(mode="pinned", verdict="lost", version="3.3.0"):
    return {
        "mode": mode,
        "created": "2026-11-26T03:17:00+00:00",
        "scenarios": {"a/x": {"title": "x"}},
        "subjects": {
            "oi": {
                "kind": "library",
                "versions": {"pkg": version},
                "cells": {
                    "a/x": {
                        "verdict": verdict,
                        "checks": [{"id": "span.ended", "verdict": verdict}],
                    }
                },
            }
        },
    }


def site(tmp_path, verdict="lost", day="2026-11-26"):
    out = tmp_path / f"site-{day}"
    out.mkdir()
    pinned = doc(verdict=verdict)
    pinned["created"] = f"{day}T03:17:00+00:00"
    (out / "pinned.json").write_text(json.dumps(pinned))
    (out / "latest.json").write_text(json.dumps(doc("latest", verdict)))
    for name in snapshots.PAGES:
        (out / name).write_text(
            f'<!doctype html><html><head><title>t</title></head><body><main><p id="f">{name} {day}'
            "</p></main></body></html>"
        )
    return out


def publish(site_dir, state):
    """What the workflow does: the published snapshots/ become the next run's state."""
    if state.exists():
        shutil.rmtree(state)
    (site_dir / "snapshots").rename(state)


def test_the_fingerprint_moves_with_a_finding_or_a_version_and_not_with_the_date():
    base = snapshots.fingerprint(doc(), doc("latest"))
    later = doc()
    later["created"] = "2027-01-01T00:00:00+00:00"
    assert snapshots.fingerprint(later, doc("latest")) == base
    assert snapshots.fingerprint(doc(verdict="pass"), doc("latest")) != base
    assert snapshots.fingerprint(doc(version="3.4.0"), doc("latest")) != base


def test_the_first_run_writes_a_full_copy_with_a_banner_and_an_index(tmp_path):
    s = site(tmp_path)
    assert snapshots.run(s, tmp_path / "none") == 0
    copy = (s / "snapshots" / "2026-11-26" / "index.html").read_text()
    assert "Snapshot of 2026-11-26" in copy and 'id="f"' in copy
    assert '<meta name="robots" content="noindex">' in copy
    index = json.loads((s / "snapshots" / "index.json").read_text())
    assert [e["date"] for e in index["snapshots"]] == ["2026-11-26"]
    assert "2026-11-26/index.html" in (s / "snapshots" / "index.html").read_text()


def test_a_day_with_the_same_results_forwards_to_the_copy_and_keeps_the_anchor(tmp_path):
    state = tmp_path / "state"
    first = site(tmp_path, day="2026-11-26")
    snapshots.run(first, state)
    publish(first, state)
    second = site(tmp_path, day="2026-11-27")
    snapshots.run(second, state)
    stub = (second / "snapshots" / "2026-11-27" / "index.html").read_text()
    assert 'location.replace("../2026-11-26/index.html" + location.hash)' in stub
    assert (
        "index.html 2026-11-26" in (second / "snapshots" / "2026-11-26" / "index.html").read_text()
    )
    publish(second, state)
    third = site(tmp_path, verdict="pass", day="2026-11-28")
    snapshots.run(third, state)
    entries = json.loads((third / "snapshots" / "index.json").read_text())["snapshots"]
    assert [(e["date"], e["copy"]) for e in entries] == [
        ("2026-11-26", "2026-11-26"),
        ("2026-11-27", "2026-11-26"),
        ("2026-11-28", "2026-11-28"),
    ]


def test_the_number_of_copies_and_dates_is_bounded(monkeypatch):
    monkeypatch.setattr(snapshots, "MAX_COPIES", 2)
    monkeypatch.setattr(snapshots, "MAX_DATES", 3)
    entries = []
    for day, print_ in [
        ("01", "a"),
        ("02", "a"),
        ("03", "b"),
        ("04", "c"),
        ("05", "c"),
        ("06", "c"),
    ]:
        entries = snapshots.plan(entries, f"2026-11-{day}", print_)
    assert [(e["date"][-2:], e["copy"][-2:]) for e in entries] == [
        ("04", "04"),
        ("05", "04"),
        ("06", "04"),
    ]
    entries = snapshots.plan(entries, "2026-11-07", "d")
    assert {e["copy"][-2:] for e in entries} == {"04", "07"}


def test_a_lost_copy_stops_the_run_rather_than_publishing_without_it(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "index.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "snapshots": [{"date": "2026-11-25", "copy": "2026-11-25", "fingerprint": "x"}],
            }
        )
    )
    with pytest.raises(snapshots.SnapshotError, match="would be lost"):
        snapshots.run(site(tmp_path), state)


def test_an_index_entry_that_is_not_a_date_is_refused(tmp_path):
    (tmp_path / "index.json").write_text(
        json.dumps({"snapshots": [{"date": "../../etc", "copy": "x", "fingerprint": "f"}]})
    )
    with pytest.raises(snapshots.SnapshotError):
        snapshots.load_index(tmp_path / "index.json")


def test_the_command_writes_the_snapshots(tmp_path, capsys):
    s = site(tmp_path)
    assert cli.main(["snapshot", "--site", str(s), "--state", str(tmp_path / "none")]) == 0
    assert "1 full copies" in capsys.readouterr().out


def test_the_nightly_job_restores_the_copies_and_keeps_todays():
    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "report.yml").read_text())
    steps = workflow["jobs"]["run"]["steps"]
    names = [s.get("name", s.get("uses", "")) for s in steps]
    fetch, keep = names.index("Fetch the kept snapshots"), names.index("Keep a dated copy")
    assert names.index("Render") < fetch < keep
    assert names[keep + 1].startswith("actions/upload-pages-artifact")
    assert "404) rm -f snapshots-state/index.json; exit 0" in steps[fetch]["run"]
    assert "^[0-9]{4}-[0-9]{2}-[0-9]{2}$" in steps[fetch]["run"]  # a copy name is a date
    assert "spantruth snapshot --site site --state snapshots-state" in steps[keep]["run"]
    for page in snapshots.PAGES:
        assert page in steps[fetch]["run"]


def test_a_second_run_on_the_same_date_keeps_the_published_copy(tmp_path):
    state = tmp_path / "state"
    first = site(tmp_path, day="2026-11-26")
    snapshots.run(first, state)
    publish(first, state)
    again = site(tmp_path, verdict="pass", day="2026-11-26-again")
    pinned = json.loads((again / "pinned.json").read_text())
    pinned["created"] = "2026-11-26T18:00:00+00:00"  # a push to main, later the same day
    (again / "pinned.json").write_text(json.dumps(pinned))
    snapshots.run(again, state)
    copy = (again / "snapshots" / "2026-11-26" / "index.html").read_text()
    assert "index.html 2026-11-26</p>" in copy  # the first run's page, not the second's
    entries = json.loads((again / "snapshots" / "index.json").read_text())["snapshots"]
    assert [(e["date"], e["copy"]) for e in entries] == [("2026-11-26", "2026-11-26")]


def test_the_copies_fit_in_the_byte_cap_and_the_newest_stays(monkeypatch):
    monkeypatch.setattr(snapshots, "MAX_BYTES", 250)
    entries = [
        {"date": d, "copy": d, "fingerprint": d} for d in ("2026-11-01", "2026-11-02", "2026-11-03")
    ]
    sizes = {"2026-11-01": 100, "2026-11-02": 100, "2026-11-03": 100}
    assert [e["copy"] for e in snapshots.cap(entries, sizes)] == ["2026-11-02", "2026-11-03"]
    assert [e["copy"] for e in snapshots.cap(entries[2:], {"2026-11-03": 999})] == ["2026-11-03"]
