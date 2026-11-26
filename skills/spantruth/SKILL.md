---
name: spantruth
license: Apache-2.0
description: Use after adding or changing LLM tracing, OpenTelemetry GenAI instrumentation, or token and cost tracking in a Python project, and when a cost dashboard or a token count looks wrong. Checks with spantruth what the instrumentation reports against what a local fake provider sent, with no API key and no network. Not for evaluating model output.
---

# spantruth

spantruth checks whether LLM telemetry is true. A local fake provider serves OpenAI and Anthropic
responses, and spantruth compares what the instrumentation reports on its spans with what was sent.

It needs [uv](https://docs.astral.sh/uv/). It needs no API key, and it makes no call to a provider. The
commands name a version, so that a new release cannot change what this skill runs until the skill moves.

## A project that uses an instrumentation library

Run this in the project directory:

```sh
uvx spantruth@0.5.0 doctor --json
```

It reads the lock file and runs each library it recognizes at the project's versions. It does not run
the project's code.

| Exit code | Meaning | What to do |
| --- | --- | --- |
| 0 | No problem found. | Say which libraries and versions were checked. |
| 1 | A problem found. | Read `subjects[].classes`, and report each class. |
| 2 | Nothing to check, such as no known library. | For a wrapper of the project's own, go to the next section. |
| 3 | spantruth could not run something. | This is not a result. Read the message, and do not report a pass. |

Each class has an `id`, a `name`, a `summary`, a `remedy`, a `largest_cost_error` in percent, an `example`,
`upstream` links, and `upstream_issues` with each link's `status` and `note`. Report them as they are. A
cost figure is for the token mix of one scenario at list price, so do not apply it to the whole
application.

`uncovered` lists the LLM libraries in the project that spantruth does not cover yet, each with a
`title`, its `packages` and the `issue` where the user can ask for it. Say that spantruth did not check
them, and give the issue link. It is in every document that `--json` prints. With exit code 2 there may
be no document, such as for a lock file that cannot be read: then read the message on stderr.

The classes come largest cost error first. When a class has an `upgrade`, the library has released the
fix: suggest that command first. It uses the project's own tool, such as `uv add`.

When `remedy` has a value, the project can act today. When an upstream issue has the status
`fixed-upstream`, the library has fixed it: suggest the upgrade that its `note` names. Otherwise check for
a newer release of the library in the `upstream` link. Ask before you change a version.

## A wrapper or instrumentation that the project wrote itself

Follow [the guide](https://github.com/Plexito-de/spantruth/blob/main/docs/test-your-wrapper.md):

1. Add a `spantruth` directory with `subject.toml` and `instrument.py`.
2. Run `uvx spantruth@0.5.0 run --subject ./spantruth --with . --out results.json`.
3. Read the findings with `uvx spantruth@0.5.0 report results.json --markdown`.

## Reading a finding

The ten bug classes are at <https://plexito-de.github.io/spantruth/bugs.html>. Before you call a finding a
bug in somebody's library, read the code that causes it. A finding is a claim about a version and a
scenario, and it cites the rule it applies.
