# PLAN_FEAT: reach the people who pay the bill

Status: approved by Christopher on 2026-11-06 ("i like all ideas"). Started the same day. Parent plan:
[PLAN.md](../PLAN.md), which this changes for release 0.4 (see "What moves").

## Why

spantruth has 4 stars and 25 unique visitors in 14 days. About 50 maintainers decide whether it is
credible, but stars and adoption need thousands of application engineers. Today the tool has nothing to
say to an engineer who does not maintain an instrumentation library: the report grades libraries, not
their stack, and it speaks in check IDs, not in money.

So 0.4 answers one question for a new audience: **what does my dashboard get wrong, and how much is that
in money?**

## Work, in order

Each item is one pull request, and each one is useful without the next.

| # | Change | User story and acceptance | Modules |
| --- | --- | --- | --- |
| 1 | Bug classes and cost impact | As a reader of the report, I see a named bug and what it does to my cost numbers. Each finding carries a class. A cell shows the real and the reported cost at list price. `bugs.html` lists the ten classes with live counts | `bugclasses.py`, `impact.py`, `data` price table, `site.py`, `report.py` |
| 2 | `spantruth doctor` | As an application engineer, I run one command in my project and learn what my exact versions get wrong. It reads `uv.lock`, `poetry.lock`, `requirements*.txt`, or the current environment. It keeps the SDK's self-tracing as my app has it. Exit codes as `run` | `doctor.py`, `cli.py` |
| 3 | Pull request comment in the action | As a maintainer who adopts the action, every pull request shows which cells changed, in one comment that updates. Fork pull requests get a warning and no failure | `report.py`, `cli.py`, `action.yml` |
| 4 | Release watcher | As a reader, I subscribe to a feed that says when a new release changes a cell, and who gets the credit. The nightly run appends one history line per subject, which is the data for the Q1 report | `watch.py`, `cli.py`, `report.yml` |
| 5 | Guides and agent skill | As an engineer with an in-house wrapper, I test it in five minutes. As a coding agent, I find the tool and how to read its result | `docs/`, `skills/spantruth/`, `llms.txt` |
| 6 | Vendor subjects | Langfuse and Sentry, then whichever other vendor exports spans we can read | `subjects/<name>/` |
| 7 | The write-up and its assets | "Your Anthropic spans are counted twice", with a measured backend receipt. Social preview image | `docs/site/`, `docs/assets/` |
| 8 | Release 0.4.0 | PyPI and the action tag | version, CHANGELOG |

Outside this repository, in `plexito-oss-contribution`: the OpenTelemetry blog draft, the joint post with
Tymur, the SIG agenda item, the newsletter text, the X and LinkedIn text, and the link pack for Hacker
News. The State of LLM Telemetry report comes in Q1 2027, from the history that item 4 starts to collect.

## Decisions

**Cost is shown at list price, for the token mix of the scenario, with its source and date.** Rejected: a
single percentage with no prices. A percentage hides that the cache classes move money by a price ratio,
not by a token ratio. Also rejected: a "per $1,000" figure across a whole app, because spantruth does not
know the app's mix, and the number would be invented. The prices are data with a source, and a scenario
with a model that has no price shows no cost.

**The cost formula follows the conventions.** Input tokens include cache reads and writes, so the uncached
part is `input - read - write`, clamped at zero. A dashboard with another formula shows another number, so
the page says which formula this is. A lost span reports nothing, so it is -100% for that call. A call
that reports usage on two spans is multiplied by the number of spans, which is true only for a backend
that sums across spans. The page says so.

**Bug classes are a mapping over checks, not new checks.** Rejected: a class field written by the grader,
because expectation files and old results name checks, and those IDs never change meaning. A class is a
reading of a check, so old documents get classes too. Class IDs (`ST01` and so on) are stable under the same
policy as check IDs.

**`doctor` reuses `--with`.** It turns the versions in a lock file into overrides on the built-in subjects.
Rejected: instrumenting the user's own application. That needs their code, their keys and their network,
and spantruth is offline by design. Rejected: importing their environment into the subject. It would make
a cell depend on unrelated packages. `doctor` therefore grades their library versions, not their app, and
it says so in its output.

**`doctor` grades what the app would do about self-tracing.** The base file turns the Anthropic SDK's spans
off so that libraries are graded alone. `doctor` leaves them on, as they are in the app, unless the caller's
environment turns them off. That is how a double span shows up.

**The comment comes from the action, not from an app.** Rejected: a GitHub App. It needs hosting, and it
would hold write tokens for other people's repositories. The action uses the token the workflow gives it.

**The watcher publishes a feed, not posts.** Rejected: posting to X from CI. It needs a stored credential,
and it would post in Christopher's name with no review, which his rules forbid. The feed is public, and he
posts from it.

**No MCP server in 0.4.** An agent skill and `doctor --json` give an agent the same thing with no new
dependency. Rejected for now, because a server adds a dependency to a tool whose subject environments are
meant to stay small. Revisit when somebody asks.

## Blind spots

- **A price goes stale.** The table names its source and date on the page. CI does not fail on it.
- **A cost figure reads as a claim about a vendor.** Findings describe a version and a scenario. The bug
  pages never rank libraries, and they credit whoever reported and fixed each bug.
- **Double spans depend on the backend.** The write-up needs a measured backend, not a guess about one.
- **A fork pull request gets a read-only token.** The comment step warns and the job stays green.
- **Lock formats vary.** `doctor` reads what `tomllib` and a plain line parser can read, and says what it
  read. A file it cannot parse is a usage error, never a clean result.
- **Results on a page can be stale.** Every number in the write-up carries its version and date.

## Prior art

As of 2026-11-06. Searched: "test LLM observability instrumentation token counts accuracy tool detect wrong
token count tracing audit CLI". Nothing that checks reported values against the wire turned up. The closest
work is in [the landscape article](../.context/CONTEXT_llm_telemetry_testing_landscape.md). The ideas come
from Codecov (one comment that updates), the OWASP Top 10 for LLM applications (named, numbered, citable
classes), and Bowtie (a public matrix that gives implementers a reason to care).

Searched: "OpenAI API pricing per million tokens input cached output gpt-5 mini". Aggregators disagree, so
the table uses the two providers' own pricing pages only.

## Test names

- `test_every_check_is_classified_or_named_as_unclassified`: a new check cannot skip the mapping.
- `test_class_of_a_finding_reads_direction`: 420 for 419 is phantom, 1187 for 5802 is cache-blind.
- `test_cost_of_a_cell_uses_the_uncached_part`: the formula, with the clamp.
- `test_lost_span_is_minus_100_percent`, `test_two_reporting_spans_double_the_cost`.
- `test_unpriced_model_has_no_cost`.
- `test_doctor_reads_uv_lock_and_requirements`, `test_doctor_maps_packages_to_subjects`.
- `test_doctor_leaves_sdk_tracing_on_unless_the_caller_turned_it_off`.
- `test_doctor_refuses_a_lock_it_cannot_parse`, `test_doctor_with_no_known_library_exits_2`.
- `test_comment_has_the_marker_and_escapes_library_values`.
- `test_changes_between_two_documents`, `test_feed_is_valid_atom_and_escaped`.
- The controls stay untouched: `control-faulty/expected.toml` is not edited by any item here.

## What moves

The second half of E2 (bug-class scenarios) moves from 0.4 to 0.5, and contributors keep adding them through
issues #33, #34 and #7. [PLAN.md](../PLAN.md) records this.
