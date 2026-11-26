# PLAN_FEAT: the adoption wave (50 ideas, 2026-11-26)

Status: built on 2026-11-26 under Christopher's `/do` ("ok lets do all of them pls", "do in parallel as
much as possible"). Every stream was reviewed by a separate instance, and its blocking findings were
fixed before the merge. The streams land together in one integration pull request, because the
GitHub-write grant ran out after 100 writes (see "What landed"). Parent plan: [PLAN.md](../PLAN.md). The ranked list of 50 ideas is in the session that
wrote this plan. This file maps every idea to a work stream, a file set and an outcome.

## Why

Measured on 2026-11-26: 5 stars, 31 unique visitors in 14 days (1 from Google), 557 PyPI downloads in
the last month, and no repository outside Plexito runs the action. The bottleneck is distribution and
trust, not features. So the wave builds what an application engineer meets first (`doctor`, a browser
checker, pages titled by symptom), what a maintainer needs to trust a gate (credibility page, faster
gate, fixes credited), and the coverage that most real projects need (frameworks, Gemini,
OpenAI-compatible servers).

## Streams, and the files each one owns

Parallel agents split on file sets. Each stream works in its own worktree and branch, opens its own pull
requests, and leaves `CHANGELOG.md`, `README.md` and `PLAN.md` to the lead, who folds them in at the end
(each pull request body carries its changelog entry). `cli.py` and `pyproject.toml` are shared: each
stream adds its own small hunk, and the lead resolves conflicts at merge, as CONTRIBUTING.md says.

| Stream | Ideas | Owns |
| --- | --- | --- |
| W1 doctor | 7 dead end, 18 fix first, 39 first-run failures, 5 doctor in the action for application repos | `doctor.py`, `tests/test_doctor.py`, `action.yml`, `tests/test_action.py` |
| W2 history | 3 version history and instant answers, 4 browser lock-file checker, 34 dated snapshots | new `history.py`, `.github/workflows/report.yml`, new `docs/site/check.*` |
| W3 site | 8 hall of fixes, 9 symptom pages, 30 credibility page, 33 preview card per finding | `site.py`, `known_issues.toml`, `bugclasses.py` text |
| W4 providers | 11 Gemini, 37 Azure OpenAI (Bedrock when time allows) | new `wire_gemini.py`, `mockserver.py`, `calls.py`, Gemini parts of `scenarios.py` and `reference.py` |
| W5 scenarios | 14 OpenAI-compatible usage shapes, 36 agent loop and concurrent calls | `wire_openai.py`, OpenAI and Anthropic parts of `scenarios.py` |
| W6 checks | 13 content capture, 35 token metrics | `checks.py`, `rules.py`, `receiver.py`, `conventions.py`, control instrument files |
| W7 frameworks | 6 LiteLLM, Pydantic AI, OpenAI Agents SDK, LangChain | subject `app.py` hook in `driver.py`, new subject directories |
| W8 devtools | 20 pytest plugin, 28 scenario pack, 47 results schema, 45 scaffolder, 29 quick gate, 31 mutation tests | new modules, `pyproject.toml` entry points |
| W9 remedies | 19 Collector fixes per bug class | new `docs/remedies/`, its test |
| W10 docs | 46 docs per audience, 32 recording, 38 plugin marketplace, 49 container image | `README.md`, `docs/*.md`, `Dockerfile`, `release.yml`, `.claude-plugin/` |
| W11 receipts | 16 wrong numbers in each backend, measured | new `results/<date>-receipts/` |
| W12 outreach | 1, 2, 15, 17, 21, 22, 23, 24, 26, 44, 48 as drafts; posts only through the pipeline | `plexito-oss-contribution/.drafts/` |
| W13 upstream | 10 fix pull requests, 1 the action proven on an OpenInference checkout | clones outside this repository |

Shared contracts, fixed before the fan-out:

- `known_issues.toml` gains three optional keys per issue: `fixed_in` (a list of requirement specs, such as
  `"openinference-instrumentation-anthropic>=3.1.0"`), `reported_on` and `fixed_on` (ISO dates). W3 adds
  them and their values; W1 reads `fixed_in` with a default.
- No stream changes the meaning of a scenario ID or a check ID. A new meaning gets a new ID.
- A new check gets a rule in `rules.py` with a permalink, and a planted fault in `control-faulty`.

## Outside this wave

- 12 (JavaScript) and 27 (Logfire): another session owns #74 and merged #14 on 2026-11-26.
- 25 co-maintainers, 40 a neutral home, 41 DCO instead of the CLA, 43 an email digest (needs a vendor
  account), 50 a talk: Christopher's decisions. They go into the report as `[NEXT]` with the tradeoff.
- 21 Hacker News and 22 newsletters are posted by Christopher by hand. W12 writes the text.

## Decisions

**Instant answers come from data that ships with the release, not from a fetch.** `doctor` stays offline
after install. Rejected: fetching the nightly table from the site at run time, because it adds a network
call to a tool whose promise is "no network after install", and a stale answer needs a date either way.

**The browser checker parses the lock file in the page and sends nothing.** It reads the same version
table. Rejected: an upload endpoint, which is a hosted service and collects data, both non-goals.

**Content capture is graded against each library's own documented switch.** A library that records content
by default is not wrong for it. It is wrong when its documented off switch is set and content still
appears. Rejected: one global rule that content must be off by default, which no spec states.

**Frameworks bring their own `app.py`.** The framework makes the call, so the driver hands the scenario to
the subject's app instead of to `calls.py`, and a subject lists the scenarios it supports.

## Blind spots

- **Parallel streams collide in `scenarios.py`, `driver.py` and `cli.py`.** Each stream keeps its hunks
  additive and in its own section, and the lead merges in the order W1, W3, W2, W8, W5, W4, W6, W7.
- **A new scenario can move `control-faulty`.** That is legitimate only when a planted fault fires on the
  new scenario as designed. The pull request names each new line in `expected.toml` and the fault behind
  it.
- **A framework subject can call the network on import** (telemetry, model lists). Its environment gets
  the dead proxy, and the stream checks for raw-socket calls.
- **Outward posts share one pace budget with the running OSS sweep**: one spantruth link per day across the
  maintainer groups. W12 and W13 prepare and register, and post only what `oss-pace-gate.py` allows.

## What landed

| Stream | Result |
| --- | --- |
| W1 doctor | Built: uncovered libraries, fix first, first-run messages, `doctor --base` and `command: doctor` in the action. Opened issues #76 to #89, one per uncovered library. |
| W2 history | Built: `spantruth history`, the shipped version table, `check.html`, dated snapshots, `doctor --fast`. |
| W3 site | Built: dates and fixes in `known_issues.toml`, the fixes page, a page per bug class, the method page, preview cards. |
| W4 providers | Built: Gemini and Azure OpenAI. Not built: Bedrock. |
| W5 scenarios | Built: OpenAI-compatible usage shapes, agent loops, concurrent calls. |
| W6 checks | Built: content capture, token metrics (graded only where the library documents them). |
| W7 frameworks | Built: the `app.py` hook, LiteLLM, Pydantic AI, the OpenAI Agents SDK, LangChain twice. |
| W8 devtools | Built: pytest plugin, scenario pack, results schema, `new subject`, `--quick`, mutation tests. |
| W9 remedies | Built: Collector recipes, tested in otelcol-contrib 0.166.0. |
| W10 docs | Built: README landing page and guides, the `doctor` recording, plugin marketplace, container image. |
| W11 receipts | Built: six bug classes in Phoenix, MLflow and Jaeger. Not measured: Langfuse, Tempo, Opik. |
| W12 outreach | Drafts and a dated schedule in `plexito-oss-contribution/.drafts/`. Nothing posted: the pace caps were full on 2026-11-26. |
| W13 upstream | Every open finding already had a fix in flight, so one new fix is ready (mlflow, Anthropic finish reasons), not posted. The action is proven on an OpenInference checkout. |
| W14 sentry | Added during the wave: the Sentry subject reads Sentry SDK 3.x. |

Found while merging: content capture and the agent loop met in the reference instrumentation (an SDK
message object in the prompt), two planted faults met on Gemini's thinking cells (a negative histogram
value), and the history table did not map Azure to the OpenAI packages. Each fix has a test.
