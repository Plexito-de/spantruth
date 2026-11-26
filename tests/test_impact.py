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


def test_cost_prices_one_hour_writes_at_their_own_rate():
    wire = truth(
        input_tokens=6288,
        output_tokens=389,
        cache_read_tokens=2861,
        cache_write_tokens=2120,
        cache_write_long_tokens=1663,
    )
    checks = [
        check("input_tokens", "pass", 6288, 6288),
        check("output_tokens", "pass", 389, 389),
        check("cache_read_tokens", "pass", 2861, 2861),
        check("cache_write_tokens", "pass", 2120, 2120),
    ]
    result = impact.cell_cost(wire, checks)
    # 1307 uncached at $2, 2861 read at $0.10, 457 written for 5 minutes at $2.50,
    # 1663 written for 1 hour at $4, 389 output at $10, per million.
    assert result.real == pytest.approx(
        (1307 * 2 + 2861 * 0.10 + 457 * 2.50 + 1663 * 4 + 389 * 10) / 1_000_000
    )
    assert result.percent == 0


def test_a_missing_write_loses_its_one_hour_price():
    wire = truth(
        input_tokens=6288,
        output_tokens=389,
        cache_read_tokens=2861,
        cache_write_tokens=2120,
        cache_write_long_tokens=1663,
    )
    checks = [
        check("input_tokens", "pass", 6288, 6288),
        check("output_tokens", "pass", 389, 389),
        check("cache_read_tokens", "pass", 2861, 2861),
        check("cache_write_tokens", "missing", 2120),
    ]
    result = impact.cell_cost(wire, checks)
    # The dashboard prices the 2120 written tokens as uncached input at $2.
    lost = 457 * (2.50 - 2) + 1663 * (4 - 2)
    assert result.real - result.reported == pytest.approx(lost / 1_000_000)


def test_a_wrong_write_count_keeps_the_wire_share_of_one_hour_writes():
    wire = truth(
        input_tokens=6288,
        output_tokens=389,
        cache_write_tokens=2120,
        cache_write_long_tokens=1060,
    )
    checks = [
        check("input_tokens", "pass", 6288, 6288),
        check("output_tokens", "pass", 389, 389),
        check("cache_write_tokens", "wrong", 2120, 4240),
    ]
    # Twice the writes, half of them for 1 hour: 2120 at $2.50 and 2120 at $4. That leaves 2048
    # uncached at $2.
    shown = (2048 * 2 + 2120 * 2.50 + 2120 * 4 + 389 * 10) / 1_000_000
    assert impact.cell_cost(wire, checks).reported == pytest.approx(shown)


def test_every_model_has_a_one_hour_write_price_at_least_the_five_minute_one():
    for price in impact.load_prices().values():
        assert price.cache_write_long >= price.cache_write


ANTHROPIC_USAGE = {
    "input_tokens": 1307,
    "output_tokens": 389,
    "cache_read_input_tokens": 2861,
    "cache_creation_input_tokens": 2120,
    "cache_creation": {"ephemeral_5m_input_tokens": 457, "ephemeral_1h_input_tokens": 1663},
    "output_tokens_details": {"thinking_tokens": 211},
}
OPENAI_USAGE = {
    "prompt_tokens": 5379,
    "completion_tokens": 377,
    "total_tokens": 5756,
    "prompt_tokens_details": {"cached_tokens": 4096, "audio_tokens": 0},
    "completion_tokens_details": {"reasoning_tokens": 64, "rejected_prediction_tokens": 12},
}


@pytest.mark.parametrize(
    ("provider", "response"),
    [
        ("anthropic", {"usage": ANTHROPIC_USAGE}),
        # The tiers and the server tools at their standard values, as the API sends them.
        (
            "anthropic",
            {
                "usage": ANTHROPIC_USAGE
                | {
                    "service_tier": "standard",
                    "inference_geo": "global",
                    "speed": "standard",
                    "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
                }
            },
        ),
        ("anthropic", {"usage": ANTHROPIC_USAGE | {"service_tier": None, "speed": None}}),
        ("openai", {"service_tier": "default", "usage": OPENAI_USAGE}),
        ("openai", {"usage": OPENAI_USAGE}),
        # A chunk before the usage chunk bills nothing, so its tier says nothing yet.
        ("openai", {"service_tier": "auto", "usage": None}),
    ],
)
def test_a_call_at_the_standard_rates_has_no_unpriced_field(provider, response):
    assert impact.unpriced(provider, response) == []


@pytest.mark.parametrize(
    ("provider", "response", "named"),
    [
        ("anthropic", {"usage": ANTHROPIC_USAGE | {"service_tier": "batch"}}, "service_tier"),
        ("anthropic", {"usage": ANTHROPIC_USAGE | {"service_tier": "priority"}}, "service_tier"),
        ("anthropic", {"usage": ANTHROPIC_USAGE | {"inference_geo": "us"}}, "inference_geo"),
        ("anthropic", {"usage": ANTHROPIC_USAGE | {"speed": "fast"}}, "speed"),
        (
            "anthropic",
            {"usage": ANTHROPIC_USAGE | {"server_tool_use": {"web_search_requests": 2}}},
            "server_tool_use.web_search_requests",
        ),
        ("openai", {"service_tier": "flex", "usage": OPENAI_USAGE}, "service_tier"),
        ("openai", {"service_tier": "priority", "usage": OPENAI_USAGE}, "service_tier"),
        (
            "openai",
            {"usage": OPENAI_USAGE | {"prompt_tokens_details": {"audio_tokens": 40}}},
            "prompt_tokens_details.audio_tokens",
        ),
        (
            "openai",
            {"usage": {"input_tokens": 90, "input_tokens_details": {"cache_write_tokens": 70}}},
            "input_tokens_details.cache_write_tokens",
        ),
        ("openai", {"usage": OPENAI_USAGE | {"a_field_of_next_year": 5}}, "a_field_of_next_year"),
    ],
)
def test_a_field_that_changes_the_price_is_named(provider, response, named):
    assert [field.split("=")[0] for field in impact.unpriced(provider, response)] == [named]


@pytest.mark.parametrize(
    "extra",
    [
        # A web fetch bills through its tokens only.
        {"server_tool_use": {"web_fetch_requests": 3, "web_search_requests": 0}},
        {"iterations": []},
    ],
)
def test_a_field_with_no_charge_of_its_own_is_not_named(extra):
    assert impact.unpriced("anthropic", {"usage": ANTHROPIC_USAGE | extra}) == []


def test_code_execution_is_named_because_it_bills_by_time():
    usage = ANTHROPIC_USAGE | {"server_tool_use": {"code_execution_requests": 1}}
    assert impact.unpriced("anthropic", {"usage": usage}) == [
        "server_tool_use.code_execution_requests=1"
    ]


def test_a_provider_with_no_price_rules_is_an_error_that_says_so():
    with pytest.raises(ValueError, match="mistral"):
        impact.unpriced("mistral", {"usage": {"prompt_tokens": 5}})


def test_an_openai_cache_write_is_priced_at_its_own_rate():
    wire = {
        "request_model": "gpt-5.6-luna",
        "error": None,
        "input_tokens": 6619,
        "output_tokens": 401,
        "cache_read_tokens": 2753,
        "cache_write_tokens": 3181,
    }
    checks = [
        check("input_tokens", "pass", 6619, 6619),
        check("output_tokens", "pass", 401, 401),
        check("cache_read_tokens", "pass", 2753, 2753),
        check("cache_write_tokens", "missing", 3181),
    ]
    result = impact.cell_cost(wire, checks)
    # 685 uncached at $0.20, 2753 read at $0.02, 3181 written at $0.25, 401 output at $1.20.
    assert result.real == pytest.approx(
        (685 * 0.20 + 2753 * 0.02 + 3181 * 0.25 + 401 * 1.20) / 1_000_000
    )
    # A missing write count prices the 3181 tokens as uncached input instead.
    assert result.real - result.reported == pytest.approx(3181 * (0.25 - 0.20) / 1_000_000)


def test_a_chat_completions_cache_write_count_is_priced():
    usage = OPENAI_USAGE | {
        "prompt_tokens_details": {"cached_tokens": 4096, "cache_write_tokens": 7}
    }
    assert impact.unpriced("openai", {"usage": usage}) == []
