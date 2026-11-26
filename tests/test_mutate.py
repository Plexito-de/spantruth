"""Every mutant of scripts/mutate.py still applies, so the weekly run never skips a line."""

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mutate", REPO / "scripts" / "mutate.py")
mutate = importlib.util.module_from_spec(spec)
sys.modules["mutate"] = mutate  # the dataclasses in it look their module up
spec.loader.exec_module(mutate)


def test_every_mutant_changes_exactly_one_place():
    for m in mutate.MUTANTS:
        source = (REPO / m.path).read_text()
        assert source.count(m.old) == 1, f"{m.name}: {m.old!r} in {m.path}"
        assert m.old != m.new, m.name
        compile(source.replace(m.old, m.new), m.path, "exec")


def test_mutant_names_are_unique():
    names = [m.name for m in mutate.MUTANTS]
    assert len(names) == len(set(names)) == 22
