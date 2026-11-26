"""Every results document matches schema/results.schema.json: the committed ones and a fresh run."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from spantruth import checks, runner, scenarios, subjects

REPO = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((REPO / "schema" / "results.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


def results_documents() -> list[Path]:
    """The results documents under results/. Other JSON files there are raw measurements."""
    found = []
    for path in sorted((REPO / "results").rglob("*.json")):
        data = json.loads(path.read_text())
        if isinstance(data, dict) and {"scenarios", "subjects"} <= data.keys():
            found.append(path)
    return found


def errors(doc: dict) -> list[str]:
    return [
        f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in VALIDATOR.iter_errors(doc)
    ]


def test_the_schema_is_valid_and_has_the_runners_version():
    Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["properties"]["schema"]["const"] == runner.SCHEMA


def test_the_verdicts_are_the_graders():
    assert SCHEMA["$defs"]["verdict"]["enum"] == list(checks.SEVERITY)


def test_the_truth_has_every_field_of_the_model():
    truth = scenarios.truth(scenarios.get("openai/basic"))
    assert set(SCHEMA["$defs"]["truth"]["properties"]) == set(vars(truth))


def test_there_are_committed_results_documents():
    assert len(results_documents()) >= 2


@pytest.mark.parametrize("path", results_documents(), ids=lambda p: str(p.relative_to(REPO)))
def test_a_committed_results_document_matches_the_schema(path):
    assert errors(json.loads(path.read_text())) == []


def test_a_document_that_breaks_the_schema_is_caught():
    doc = json.loads(results_documents()[-1].read_text())
    cell = next(iter(next(iter(doc["subjects"].values()))["cells"].values()))
    cell["verdict"] = "fine"
    del cell["checks"][0]["rule_url"]
    found = errors(doc)
    assert any("'fine' is not one of" in e for e in found)
    assert any("'rule_url' is a required property" in e for e in found)


@pytest.mark.slow
@pytest.mark.external
@pytest.mark.timeout(600)
def test_a_fresh_control_run_matches_the_schema(tmp_path):
    controls = [subjects.load("control-honest"), subjects.load("control-faulty")]
    ids = ["openai/basic", "openai/rate-limited", "anthropic/stream-early-exit"]
    doc = runner.run(controls, scenarios.with_dependencies(ids), tmp_path)
    # Through JSON, as the CLI writes it: tuples become arrays.
    assert errors(json.loads(json.dumps(doc))) == []
