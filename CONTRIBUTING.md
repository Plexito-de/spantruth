# Contributing to spantruth

Thank you for your interest. spantruth gets better with every library, provider and failure mode
that somebody adds, and with every maintainer who tells us where a check is unfair.

## Ways to help

These are the contributions the project needs most. Each one has an issue with the
[`help wanted`](https://github.com/Plexito-de/spantruth/labels/help%20wanted) label.

- **Add a subject.** MLflow, LiteLLM, and the JavaScript libraries
  (OpenLLMetry JS, OpenInference JS, the Vercel AI SDK) are not covered yet.
- **Add a provider.** AWS Bedrock, Google Gemini and the OpenAI Responses API come next.
- **Add a scenario.** Anthropic thinking tokens, and tool calls in a stream.
- **Maintain an instrumentation library?** Review the adapter for your library in
  `subjects/<name>/instrument.py`. If it does not use your documented setup, the results are not
  fair, and we want to know.
- **Dispute a finding.** Open an issue with the **Dispute a finding** form. If the rule we cite
  does not say what we claim, we fix the check. If the spec is unclear, we mark the cell as
  disputed in `known_issues.toml` and link the discussion.

## Set up

You need [uv](https://docs.astral.sh/uv/) and Python 3.11 or later.

```sh
git clone https://github.com/Plexito-de/spantruth.git
cd spantruth
uv sync
uv run pytest -m "not slow"     # unit tests, about 5 seconds
uv run pytest -m slow           # both controls end to end, about 2 minutes
uv run ruff check . && uv run ruff format --check .
```

`uvx pre-commit install` runs the linters on every commit.

Keep `XDG_CACHE_HOME` outside the checkout. The subject environments live under it, and a
cache inside the tree goes into the source distribution, which fails
`test_wheel_runs_a_control_without_a_clone`.

## Add a subject

1. Create `subjects/<name>/subject.toml` with `title`, `homepage`, `providers`, the pinned
   `packages`, and the package names to `track` in the nightly run of the latest versions.
2. Create `subjects/<name>/instrument.py` with one function:

   ```python
   def instrument(tracer_provider, otlp_endpoint):
       # The library's documented quickstart, pointed at tracer_provider or otlp_endpoint.
       return None
   ```

   The subject's `track` packages also tell `spantruth doctor` that a project uses this library. Set
   `detect` in `subject.toml` when other packages mean the same, such as a meta-package.

   Use the setup that the library's own documentation shows first. Return nothing, or a list of
   extra tracer providers to flush when the library makes its own.

   Every subject gets the SDK versions in `subjects/_base.toml`. When a library declares that it
   cannot run them, list the newest versions it accepts in its own `packages`, which replace the
   base ones, and say why in `notes`. Never force a version the library excludes.
3. Run it: `uv run spantruth run --subject <name>`. A subject directory outside `subjects/` runs
   the same way, by its path: `--subject ./path/to/subject`.
4. Read every red cell before you open the pull request. A red cell is a claim about somebody's
   library. Find the line of code that causes it, or tell us in the pull request that you could
   not.

## Add a framework

A framework such as LiteLLM or Pydantic AI makes the provider call itself, so the app calls the
framework and not the SDK. Such a subject also brings an `app.py` with one function:

```python
def run(scenario, base_url, seen):
    # Make the scenario's call through the framework, pointed at base_url.
    # Record what the app saw on `seen` (a `spantruth.calls.Seen`), as calls.py does.
    ...
```

The driver calls it in the baseline run and in the instrumented run, so `app.unchanged` still
compares the two. Keep the instrumentation in `instrument.py`, not in `app.py`: the baseline run
must not trace. Exceptions propagate, as in `calls.py`.

A framework that ends its spans after the call returns, in a thread or a queue, can also define
`settle()` in `app.py`. The instrumented run calls it after the call and before it reads which
spans ended, so a span that the framework ends later is not reported as lost.

List the scenarios that the framework can make in `subject.toml`, such as
`scenarios = ["openai/basic", "openai/stream"]`. The other scenarios are not run and have no cell.
Only a subject with an `app.py` can list scenarios: a library that instruments the SDK meets every
call that the SDK makes.

A scenario with `calls`, such as an agent loop, makes several calls in one trace. For it, the app
must call `seen.add(one, start_ns, end_ns)` once per call, with what the app saw of that call and
when it ran, and send the message history from call to call the way `calls._loop` does. No app does
that yet, so `subject.toml` cannot list such a scenario for a subject with an `app.py`.

## Add a scenario

1. Build the response in `src/spantruth/scenarios.py` with the builders in `wire_openai.py` or
   `wire_anthropic.py`. Give every token count a value that no other count in the scenario has.
2. If the scenario needs a new kind of call, add it to `src/spantruth/calls.py`.
3. Run `uv run pytest`. `test_fixtures_parse_with_sdk_types` proves that the provider's SDK
   accepts your fixture.
4. Run the controls. `control-honest` must pass the new scenario. If it does not, fix the
   reference instrumentation or the check, never the expected result.

## Stable IDs

A scenario ID, such as `anthropic/stream`, and a check ID, such as `output_tokens`, never change
meaning. Maintainers keep expectation files in their own repositories that name them, so a change
of meaning would silently change their CI. A new meaning gets a new ID.

## How we review

A contributor should never have to guess when they are done. So a maintainer's review follows these
rules:

1. **The first review is complete.** It names every change that blocks the merge, in one comment.
   Anything not in it does not block.
2. **Later rounds check only those changes**, plus anything the new code itself breaks. A new idea
   after the first review is not a new condition. The maintainer opens an issue for it or does it
   after the merge. The one exception is a real problem the first review overlooked, such as a wrong
   verdict or a broken control. It may block, and the review says plainly that it should have been
   raised in the first round.
3. **A conflict that `main` caused is the maintainer's to resolve**, not the contributor's.
4. **Optional suggestions are marked as optional**, and they never hold a merge.

## Pull requests

- One topic per pull request. Write the title as a
  [Conventional Commit](https://www.conventionalcommits.org/): `feat(subjects): add logfire`.
- Add a test with every change in behavior. A test must fail when you revert the change.
- Update `CHANGELOG.md` under **Unreleased**.

## Contributor License Agreement

Before we can merge your first pull request, you sign the
[Plexito Contributor License Agreement](https://github.com/Plexito-de/.github/blob/main/cla/INDIVIDUAL.md).
A bot asks you on the pull request, and you sign with one comment. It is a license, not a
transfer: you keep the copyright in your work. If you contribute for your employer, your employer
signs the [Entity Agreement](https://github.com/Plexito-de/.github/blob/main/cla/ENTITY.md) first.

## Code of Conduct

This project follows the
[Plexito Code of Conduct](https://github.com/Plexito-de/.github/blob/main/CODE_OF_CONDUCT.md).
Report unacceptable behavior to [info@plexito.de](mailto:info@plexito.de).
