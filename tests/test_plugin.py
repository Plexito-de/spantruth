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
