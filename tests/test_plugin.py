"""The repository is a Claude Code plugin marketplace that ships the spantruth skill."""

import json
import re
from pathlib import Path

import spantruth

REPO = Path(__file__).resolve().parents[1]
MARKETPLACE = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())


def _plugin():
    (plugin,) = MARKETPLACE["plugins"]
    return plugin


def test_the_plugin_source_is_the_skill_directory():
    # A relative source with SKILL.md at its root loads as one skill, and an install copies only
    # that directory, not the whole repository.
    source = REPO / _plugin()["source"]
    assert source == REPO / "skills" / "spantruth"
    assert (source / "SKILL.md").is_file()


def test_the_plugin_version_is_the_package_version():
    # Claude Code keeps users on the version in the entry until it changes, so a release that
    # does not move it would never reach them.
    assert _plugin()["version"] == spantruth.__version__


def test_the_skill_runs_the_version_it_ships_with():
    skill = (REPO / "skills" / "spantruth" / "SKILL.md").read_text()
    pins = set(re.findall(r"uvx spantruth@([0-9][^ ]*) ", skill))
    assert pins == {spantruth.__version__}


def test_the_install_names_are_the_documented_ones():
    # The docs tell users to run `/plugin install spantruth@spantruth`.
    assert MARKETPLACE["name"] == "spantruth"
    assert _plugin()["name"] == "spantruth"


def _keys(value, prefix=""):
    """Every key in a JSON value, alone and as a path with `[]` for a list item."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield f"{prefix}{key}"
            yield from _keys(inner, f"{prefix}{key}.")
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item, prefix.rstrip(".") + "[].")


def test_every_field_the_skill_names_is_in_the_doctor_json(monkeypatch):
    # The skill tells an agent which fields of `doctor --json` to read. A field that this version
    # does not print sends the agent after nothing.
    from test_doctor import FIXED, REPORTED, result_doc

    from spantruth import doctor, report

    monkeypatch.setattr(report, "known_issues", lambda: [FIXED, REPORTED])
    summary = doctor.summarize(result_doc())
    uncovered = doctor.uncovered({"litellm": "1.0"})
    assert uncovered, "the uncovered table has no litellm entry"
    document = {"source": "", "results": "", "exit_code": 1, "subjects": summary}
    printed = set(_keys({**document, "uncovered": uncovered}))
    assert "subjects[].classes[].upstream_issues[].status" in printed

    skill = (REPO / "skills" / "spantruth" / "SKILL.md").read_text()
    section = skill[skill.index("## A project that uses") : skill.index("## A wrapper")]
    named = set(re.findall(r"`([a-z_]+(?:\[\]\.[a-z_]+)*)`", section))
    assert named, "the skill names no field"
    assert named <= printed, sorted(named - printed)
