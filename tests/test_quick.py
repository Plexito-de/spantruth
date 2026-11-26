"""The quick gate covers every bug class for every provider that can show it."""

from pathlib import Path

import yaml

from spantruth import bugclasses, cli, quick, scenarios

REPO = Path(__file__).resolve().parents[1]


def test_every_bug_class_has_a_shape():
    assert set(quick.SHAPES) == set(bugclasses.BY_ID)


def test_every_bug_class_has_a_quick_scenario():
    picks = quick.picks()
    for bug in bugclasses.CLASSES:
        assert picks[bug.id], f"{bug.id} {bug.name}: no scenario of any of its shapes"


def test_each_provider_gets_its_own_picks():
    providers = {sc.provider for sc in scenarios.SCENARIOS.values()}
    for bug_id, by_provider in quick.picks().items():
        assert set(by_provider) <= providers
        for provider, sid in by_provider.items():
            assert scenarios.get(sid).provider == provider, (bug_id, sid)
    # Every provider that has scenarios is in the quick run.
    assert {scenarios.get(sid).provider for sid in quick.scenario_ids()} == providers


def test_the_best_shape_wins():
    picks = quick.picks()
    # A stream with no usage shows phantom tokens best. Anthropic has none, so it falls back.
    assert picks["ST04"]["openai"] == "openai/stream-no-usage"
    assert scenarios.truth(scenarios.get(picks["ST04"]["anthropic"])).unread_counts
    # A stream that fails mid-way shows a false OK best.
    assert all(scenarios.get(sid).stream for sid in picks["ST07"].values())


def test_the_quick_run_is_smaller_and_in_registry_order():
    ids = quick.scenario_ids()
    assert len(ids) < len(scenarios.SCENARIOS)
    assert ids == [sid for sid in scenarios.SCENARIOS if sid in ids]


def test_run_quick_selects_the_quick_scenarios(monkeypatch, tmp_path):
    seen = {}

    def fake_run(selected, ids, cache, **kwargs):
        seen["ids"] = ids
        return {"schema": 1, "scenarios": {}, "subjects": {}}

    monkeypatch.setattr(cli.runner, "run", fake_run)
    monkeypatch.setattr(cli.report, "markdown", lambda doc: "")
    out = str(tmp_path / "r.json")
    assert cli.main(["run", "--subject", "control-honest", "--quick", "--out", out]) == 0
    assert seen["ids"] == scenarios.with_dependencies(quick.scenario_ids())


def test_quick_and_scenario_together_are_a_usage_error(capsys):
    args = ["run", "--subject", "control-honest", "--quick", "--scenario", "openai/basic"]
    assert cli.main(args) == 2
    assert "--quick and --scenario" in capsys.readouterr().err


def test_the_action_passes_quick_through():
    action = yaml.safe_load((REPO / "action.yml").read_text())
    assert action["inputs"]["quick"]["default"] == "false"
    (run,) = [s for s in action["runs"]["steps"] if s.get("id") == "run"]
    assert run["env"]["QUICK"] == "${{ inputs.quick }}"
    assert 'if [ "${QUICK:-}" = "true" ]; then args+=(--quick); fi' in run["run"]


# The bug classes a provider has no scenario for today. A provider that loses a class, or a new
# scenario that closes a gap, changes this table on purpose.
GAPS = {
    "azure": {"ST01", "ST03", "ST04", "ST06", "ST07", "ST08", "ST09", "ST10"},
    "gemini": {"ST08", "ST09", "ST10"},
}


def test_every_provider_gets_every_class_it_can_show():
    picks = quick.picks()
    providers = {sc.provider for sc in scenarios.SCENARIOS.values()}
    for provider in providers:
        missing = {c.id for c in bugclasses.CLASSES if provider not in picks[c.id]}
        assert missing == GAPS.get(provider, set()), provider


def test_no_quick_pick_is_a_special_family():
    # Content and multi-call scenarios grade other things; each class keeps a plain representative.
    for by_provider in quick.picks().values():
        for sid in by_provider.values():
            sc = scenarios.get(sid)
            assert not (sc.content or sc.calls), sid
