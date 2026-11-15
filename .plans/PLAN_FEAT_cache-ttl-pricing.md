# Plan: price 1-hour cache writes, and name every other field that changes a price

Status: built on 2026-11-15. Grounded in the code read directly: `impact.py`, `wire_anthropic.py`,
`reference.py`, `checks.py`, `scenarios.py`, `subjects/control-faulty/expected.toml`.

## Problem

`prices.toml` had one `cache_write` rate per model. For `claude-sonnet-5-5` it was the 5-minute rate
($2.50 per million, 1.25x input). A 1-hour cache write costs $4.00 (2x input), per the
[Anthropic pricing page](https://platform.claude.com/docs/en/about-claude/pricing), checked
2026-11-15. `impact.py` never read the TTL, so it priced a 1-hour write 37.5% low. No scenario sent
a 1-hour write, so no published figure was wrong. The first scenario that did would have shown a
wrong cost.

The general class: the wire carries a fact that changes the price, and the price model ignores it.

## What the wire carries that changes a price

Anthropic `Usage` in anthropic 2.4.0 has these fields beyond the four token counts:

| Field | Effect on price | Effect on the percent a wrong count causes | Action |
| --- | --- | --- | --- |
| `cache_creation.ephemeral_1h_input_tokens` | 2x input instead of 1.25x | yes: a dropped or wrong write moves the figure more | priced, two scenarios |
| `inference_geo` = `"us"` | 1.1x on every category | none, the multiplier is uniform | named by `impact.unpriced` |
| `service_tier` (`priority`, `batch`) | uniform multiplier | none | named |
| `speed` = `"fast"` | premium, not offered on Sonnet 5.5 | none | named |
| `server_tool_use.web_search_requests` | $10 per 1,000 searches, not a token | yes, and no GenAI attribute carries it | named |
| `server_tool_use.code_execution_requests` | billed by execution time | yes | named |
| `server_tool_use.web_fetch_requests` | none, a web fetch bills through its tokens | none | priced as tokens |

OpenAI, from the [pricing page](https://developers.openai.com/api/docs/pricing) and the
[prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching), both checked
2026-11-15, and the `CompletionUsage` type in openai 3.42.1:

| Field | Effect on price | Action |
| --- | --- | --- |
| `service_tier` on the response (`flex`, `priority`, `scale`) | Flex and Batch 0.5x, Priority (renamed Fast) 1.8x, uniform | named. The fixtures send `default` |
| `prompt_tokens_details.audio_tokens`, `completion_tokens_details.audio_tokens` | no rate in the table | named. The fixtures send 0 |
| `prompt_tokens_details.cache_write_tokens` | GPT-5.6 and later bill a cache write: 1.25x input for 30 minutes, 2.5x for 12 hours. `gpt-5-mini` bills none | named. No scenario yet, see "Open" |
| `completion_tokens_details.accepted_prediction_tokens`, `rejected_prediction_tokens` | inside `completion_tokens`, at the output rate | priced as output |

The guard found the OpenAI rows: the fixtures already sent `service_tier`, audio counts and
prediction counts, and nothing said what they do to a price.

## Decisions

1. `prices.toml` gets `cache_write_long` for every model: the rate for the long cache lifetime.
   `cache_write` stays the rate for the default lifetime. The name fits both providers (1 hour at
   Anthropic, 12 hours at OpenAI), so an OpenAI row needs no second schema.
2. `Truth` gets `cache_write_long_tokens`, read from `usage.cache_creation`. It stays out of
   `checks.VALUES`, so it is never graded: no GenAI convention attribute separates the lifetimes,
   so no span can be wrong about the split. It lands in the results document, where `impact` reads
   it. A results document from an older version has no such key, and `impact` reads that as zero.
3. `impact._cost` prices the long part of the writes at `cache_write_long`. The reported figure
   gives the span's write count the wire's long share, so a correct count shows 0%, and a dropped
   write loses its real price.
4. Two new scenarios with new IDs: `anthropic/cache-1h` and `anthropic/stream-cache-1h`. Each has a
   mixed write (5-minute and 1-hour) with counts no other fixture uses. The existing cache fixtures
   keep their bytes, because a scenario ID never changes meaning.
5. `impact.unpriced(provider, response)` names every field of one response that changes its price
   and has no rate. A count of zero, an absent value and a standard tier are fine. A test runs it
   on every fixture. So the next scenario that sends a batch tier, an Anthropic web search or an
   OpenAI cache write must decide its price first. It reads `usage` and the tier only, see "Open".
6. `checks._formula` names three new wrong sums on a scenario with a long write: "1-hour cache
   writes left out", "5-minute cache writes left out" and "cache writes counted twice" (a library
   that adds the TTL breakdown to `cache_creation_input_tokens`). Two parts of equal size cannot
   be told apart, so the fixture test requires them to differ.
7. `test_fixtures_parse_with_sdk_types` also fails on a built fixture with a field the SDK type
   does not declare. The SDK types accept an unknown field, so the test proved less than AGENTS.md
   says. The review of this change found the case: the builder put `cache_creation` into the usage
   of a cumulative `message_delta`, which has no such field. A recording is exempt, because a real
   stream can be ahead of the SDK.
8. `control-faulty/expected.toml` gets the two new scenarios. Its `anthropic-raw-input` fault makes
   `input_tokens` wrong there, as on every other Anthropic cache scenario. This adds rows for new
   scenarios. It does not move an existing one.

## Rejected alternatives

- **One blended `cache_write` rate per model.** Simple, but the right blend depends on each call's
  mix of lifetimes, so every figure would be wrong by a different amount.
- **Grade the lifetime split as a new check.** No GenAI semantic convention defines an attribute for
  it, and AGENTS.md requires every check to cite a rule. So the split is truth for pricing only.
- **Price the uniform multipliers (geo, tier, speed) now.** No fixture sends them, and they cannot
  change a percent. `impact.unpriced` forces the decision when a fixture first does.
- **A list of allowed field names only.** The first version of the guard failed on every OpenAI
  fixture, because they send `audio_tokens: 0`. A field at zero bills nothing, so the rule reads
  the value too.
- **Name the price `cache_write_1h`.** OpenAI's long lifetime is 12 hours, so the name would be
  wrong for the second provider that has one.
- **Refuse a cost figure at run time when the wire has an unpriced field.** Every scenario is a
  fixture in this repository, so a test over the fixtures covers every case, with no new field in
  the results document.

## Open

- **A charge outside `usage`.** OpenAI bills some built-in tool calls per call, and they are items
  of the output, not fields of `usage`. `impact.unpriced` does not name them. The rates of a long
  prompt (Claude Haiku 5.5, OpenAI's GPT-5.6 and later) follow from the input count, and no price
  row models them. No fixture has either.
- **The span's share of long writes is an assumption.** A span that reports one part of a write
  alone is priced at the wire's mix of both lifetimes. No library does this today.
- **An OpenAI cache-write scenario.** GPT-5.6 and later bill a cache write and report
  `cache_write_tokens`. A scenario needs a GPT-5.6 model in the registry with its two write rates,
  the OpenAI truth for `cache_write_tokens`, and a look at each library before the report shows a
  red cell. The 12-hour breakdown (`cache_write_tokens_details`) is in the Responses API only, and
  a Responses stream that ends is not graded yet (E3 in [PLAN.md](../PLAN.md)).

## Tests

- `test_cost_prices_one_hour_writes_at_their_own_rate`: a mixed write costs $2.50 and $4 per million.
- `test_a_missing_write_loses_its_one_hour_price`: a dropped write moves the figure by the real price.
- `test_a_wrong_write_count_keeps_the_wire_share_of_one_hour_writes`: the share follows the wire.
- `test_every_model_has_a_one_hour_write_price_at_least_the_five_minute_one`: the table is complete.
- `test_anthropic_truth_reads_the_one_hour_share_of_cache_writes`: body and stream truth.
- `test_a_fixture_without_a_ttl_breakdown_has_no_one_hour_writes`: the older fixtures are unchanged.
- `test_no_fixture_sends_a_field_the_cost_figure_does_not_price`: decision 5, on every fixture.
- `test_the_price_guard_reads_a_usage_inside_json_text`: a recorded stream is not skipped.
- `test_a_call_at_the_standard_rates_has_no_unpriced_field` and
  `test_a_field_that_changes_the_price_is_named`: the rule, in both directions.
- `test_one_hour_cache_writes_left_out_is_named`, `test_five_minute_cache_writes_left_out_is_named`,
  `test_ttl_breakdown_added_to_cache_writes_is_named`, `test_ttl_breakdown_added_to_input_tokens_is_named`,
  `test_two_equal_parts_of_a_cache_write_are_not_told_apart`.
- `test_the_fixture_check_sees_a_field_the_sdk_type_does_not_declare` and
  `test_a_cumulative_stream_keeps_the_ttl_breakdown_in_message_start`: decision 7.
- `test_the_one_hour_part_of_a_fixture_stays_inside_the_cache_write`: the builder refuses a bad split.
- Existing: `test_fixtures_parse_with_sdk_types`, `test_fixture_truth_values_are_distinct`, and the
  slow control runs.

## Verification

`uv run pytest -m "not slow"`, `uv run pytest -m slow`, `uv run ruff check . && uv run ruff format --check .`.
Each new behavior has a mutant that turns a test red. The two new scenarios also ran against every
subject, and the pull request lists the result.
