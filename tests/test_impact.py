"""What a wrong count does to a cost figure, and which named bug class a finding belongs to."""

import pytest

from spantruth import bugclasses, impact
from spantruth.rules import RULES

SONNET = "claude-sonnet-5-5"


def truth(**values):
    return {"request_model": SONNET, "error": None, **values}


def check(id, verdict, expected=None, got=None, detail=""):
    return {"id": id, "verdict": verdict, "expected": expected, "got": got, "detail": detail}


def test_every_check_is_classified_or_named_as_unclassified():
    named = set(bugclasses.BY_CHECK) | bugclasses.UNCLASSIFIED
    assert named >= set(RULES) | {"harness"}


def test_there_are_ten_classes_with_stable_ids():
    assert [c.id for c in bugclasses.CLASSES] == [f"ST{n:02}" for n in range(1, 11)]


@pytest.mark.parametrize(
    ("finding", "expected"),
    [
        (check("span.ended", "lost"), "ST01"),
        (check("span.recorded", "missing"), "ST01"),
        (check("tokens.single-report", "wrong", 1, 2), "ST02"),
        (check("cache_read_tokens", "wrong", 6144, 0), "ST03"),
        (check("cache_write_tokens", "missing", 737), "ST03"),
        (check("input_tokens", "wrong", 5802, 1187, "cache reads and writes left out"), "ST03"),
        (check("output_tokens", "wrong", 419, 420), "ST04"),
        (
            check("input_tokens", "wrong", None, 18, "estimated: the stream carried no usage"),
            "ST04",
        ),
        (check("output_tokens", "missing", 419), "ST05"),
        (check("input_tokens", "wrong", 500, 100), "ST05"),
        (check("reasoning_tokens", "missing", 1792), "ST06"),
        (check("error.status", "wrong", "ERROR", ["OK"]), "ST07"),
        (check("error.type", "missing"), "ST07"),
        (check("status.unset", "wrong", "UNSET", "ERROR"), "ST08"),
        (check("finish_reasons", "missing", ["stop"]), "ST09"),
        (check("app.unchanged", "broken"), "ST10"),
    ],
)
def test_class_of_a_finding_reads_direction(finding, expected):
    assert bugclasses.classify(finding).id == expected


def test_a_check_with_no_class_has_none():
    assert bugclasses.classify(check("span.parented", "wrong")) is None
    assert bugclasses.classify(check("harness", "inconclusive")) is None


def test_prices_name_a_source_for_every_model():
    for price in impact.load_prices().values():
        assert price.source.startswith("https://")


def test_cost_of_a_cell_uses_the_uncached_part():
    wire = truth(
        input_tokens=5802, output_tokens=377, cache_read_tokens=4096, cache_write_tokens=519
    )
    checks = [
        check("input_tokens", "pass", 5802, 5802),
        check("output_tokens", "pass", 377, 377),
        check("cache_read_tokens", "pass", 4096, 4096),
        check("cache_write_tokens", "pass", 519, 519),
    ]
    result = impact.cell_cost(wire, checks)
    # 1187 uncached at $2, 4096 read at $0.10, 519 written at $2.50, 377 output at $10, per million.
    assert result.real == pytest.approx(7851.1 / 1_000_000)
    assert result.reported == pytest.approx(result.real)
    assert result.percent == 0


def test_the_uncached_part_is_clamped_at_zero():
    wire = truth(
        input_tokens=5802, output_tokens=377, cache_read_tokens=4096, cache_write_tokens=519
    )
    checks = [
        check("input_tokens", "wrong", 5802, 1187),
        check("output_tokens", "pass", 377, 377),
        check("cache_read_tokens", "pass", 4096, 4096),
        check("cache_write_tokens", "pass", 519, 519),
    ]
    result = impact.cell_cost(wire, checks)
    assert result.reported < result.real
    assert result.percent == pytest.approx(-30.2, abs=0.05)


def test_a_missing_count_is_zero_on_the_dashboard():
    wire = truth(input_tokens=1000, output_tokens=100)
    checks = [check("input_tokens", "pass", 1000, 1000), check("output_tokens", "missing", 100)]
    result = impact.cell_cost(wire, checks)
    assert result.reported == pytest.approx(1000 * 2 / 1_000_000)


def test_a_small_overcount_shows_as_small():
    wire = truth(input_tokens=1000, output_tokens=419)
    checks = [check("input_tokens", "pass", 1000, 1000), check("output_tokens", "wrong", 419, 420)]
    assert impact.cell_cost(wire, checks).percent == pytest.approx(0.2, abs=0.05)


def test_lost_span_is_minus_100_percent():
    wire = truth(input_tokens=1000, output_tokens=100)
    assert impact.cell_cost(wire, [check("span.ended", "lost")]).percent == -100


def test_lost_span_of_an_early_exit_has_no_real_figure_but_still_disappears():
    wire = truth(input_tokens=1913, output_tokens=None)
    result = impact.cell_cost(wire, [check("span.ended", "lost")])
    assert result.real is None
    assert result.percent == -100


def test_a_refused_call_has_no_cost_to_lose():
    wire = truth(error="HTTP 429")
    assert impact.cell_cost(wire, [check("span.recorded", "missing")]) is None


def test_two_reporting_spans_double_the_cost():
    wire = truth(input_tokens=1000, output_tokens=100)
    checks = [
        check("input_tokens", "pass", 1000, 1000),
        check("output_tokens", "pass", 100, 100),
        check("tokens.single-report", "wrong", 1, 2),
    ]
    assert impact.cell_cost(wire, checks).percent == 100


def test_unpriced_model_has_no_cost():
    wire = {"request_model": "gpt-6-luna", "error": None, "input_tokens": 1, "output_tokens": 1}
    assert impact.cell_cost(wire, [check("span.ended", "lost")]) is None


def test_no_truth_tokens_and_no_lost_span_has_no_cost():
    wire = truth(no_usage=True)
    assert impact.cell_cost(wire, [check("input_tokens", "wrong", None, 18)]) is None
    assert impact.cell_cost(None, []) is None


def test_a_correct_cell_with_all_passing_has_no_cost_error():
    wire = truth(input_tokens=1000, output_tokens=100)
    checks = [check("input_tokens", "pass", 1000, 1000), check("output_tokens", "pass", 100, 100)]
    assert impact.cell_cost(wire, checks).percent == 0


def test_a_value_that_is_not_a_number_counts_as_nothing():
    wire = truth(input_tokens=1000, output_tokens=100)
    checks = [
        check("input_tokens", "wrong", 1000, "many"),
        check("output_tokens", "pass", 100, 100),
    ]
    assert impact.cell_cost(wire, checks).reported == pytest.approx(100 * 10 / 1_000_000)


def test_a_class_cost_does_not_borrow_the_figure_of_another_class():
    wire = truth(input_tokens=1000, output_tokens=419)
    checks = [
        check("input_tokens", "pass", 1000, 1000),
        check("output_tokens", "wrong", 419, 420),
        check("tokens.single-report", "wrong", 1, 2),
    ]
    assert impact.cell_cost(wire, checks).percent > 100
    assert impact.class_cost(wire, checks, "ST04").percent < 1
    assert impact.class_cost(wire, checks, "ST02").percent == 100


def test_a_class_that_moves_no_cost_has_no_figure():
    wire = truth(input_tokens=1000, output_tokens=419)
    checks = [check("error.status", "wrong", "ERROR", ["OK"])]
    assert impact.class_cost(wire, checks, "ST07") is None


def test_a_ghost_span_figure_needs_the_ghost_check():
    wire = truth(input_tokens=1000, output_tokens=419)
    checks = [check("span.ended", "lost"), check("output_tokens", "wrong", 419, 420)]
    assert impact.class_cost(wire, checks, "ST01").percent == -100
    assert impact.class_cost(wire, checks, "ST04").percent < 1
