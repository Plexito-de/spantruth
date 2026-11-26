# spantruth for application engineers

You use an LLM instrumentation library, such as OpenLLMetry, OpenInference or Logfire, and a
dashboard shows you tokens and cost. This page tells you how to find out what your versions get
wrong, what each bug costs you, and how to fix it.

## Why the numbers can be wrong

Cost dashboards, token budgets, alerts and audits all read the numbers that instrumentation
libraries put on spans. When a library adds a count twice or drops a cached token, nothing fails:
the dashboard shows a wrong number with full confidence. When a span never ends, the call is
missing from the trace altogether.

These bugs live on the paths that normal tests skip: a stream the app leaves early, a connection
that drops, a 429, a prompt-cache hit. spantruth tests exactly those paths, for every library,
in the same way.

## Check your project

You need [uv](https://docs.astral.sh/uv/). You need no clone, no API key and no setup. Run this in
your project:

```sh
uvx spantruth doctor
```

```text
OpenLLMetry  (anthropic 2.4.0, openai 3.42.1, opentelemetry-instrumentation-anthropic 0.62.4, ...)
  ST01 Ghost span: 5 of 45 scenarios
    The app made the call, and no finished span ever reaches a backend.
    Cost figure for one call: -100%, at list price
    For example, anthropic/stream-early-exit: never ended: anthropic.chat
    Tracked at https://github.com/traceloop/openllmetry/issues/4536
    Tracked at https://github.com/traceloop/openllmetry/issues/4643
  ST02 Double span: 14 of 45 scenarios
    One call reports its token usage on two spans.
    Cost figure for one call: +100% in a backend that sums across spans, at list price
    Today: Let one side report the call: set ANTHROPIC_OPEN_TELEMETRY=false, or pass open_telemetry=False to the client.
  ...
```

`doctor` reads `uv.lock`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, `requirements*.txt`, `.venv` or
`pyproject.toml`, in that order. It runs the libraries it recognizes at your versions, and it leaves the
Anthropic SDK's own tracing on, as your application does. It never runs your code, so it grades your
versions, not your application. The first run downloads the libraries at your versions. After that it
needs no network.

To check a project in another directory, give its path: `uvx spantruth doctor path/to/project`.

| Exit code | Meaning |
| --- | --- |
| 0 | No problem found. |
| 1 | A problem found. |
| 2 | Nothing to check, for example no library that spantruth knows. |
| 3 | spantruth could not run something. This is not a result. |

`--json` prints the result for a script or a [coding agent](../skills/spantruth/SKILL.md).

If you wrote your own wrapper around the OpenAI or Anthropic SDK, `doctor` has nothing to recognize.
[Test your wrapper in five minutes](test-your-wrapper.md) instead.

### Run it in a container

Each release from 0.6.0 on is also an image, `ghcr.io/plexito-de/spantruth:<version>`. It has Python
3.12, uv and spantruth, and runs as a user without root. Use it when you do not want to install uv:

```sh
docker run --rm -v "$PWD:/work:ro" -v spantruth-cache:/cache ghcr.io/plexito-de/spantruth:0.6.0 doctor
```

- `-v "$PWD:/work:ro"` gives `doctor` your project, read-only. It reads the lock file there and writes
  nothing into it.
- `-v spantruth-cache:/cache` keeps the library environments and the results in a volume. The first run
  downloads the libraries at your versions from PyPI. The next runs use the volume.
- Add `--json` after `doctor` for the JSON result. The exit codes are the same.

The image takes every other command too, such as `list` or
`run --subject openllmetry --out /cache/results.json`.

## What the bug classes cost

Every finding belongs to one of [ten named bug classes](https://plexito-de.github.io/spantruth/bugs.html),
such as a ghost span (the span never ends, so the call disappears), a double span (usage on two spans), or
phantom tokens. Where a count is wrong, the report also shows what that does to a cost figure at list
price.

The cost figure in the `doctor` output is for the token mix of one scenario at list price. It shows the
size of the error on one call. Do not apply it to your whole bill: your own mix of calls decides that.

- A **ghost span** shows `-100%`: the call costs money, and no backend ever sees it.
- A **double span** shows `+100%` in a backend that sums token counts across spans.
- A wrong token count, such as cached tokens added twice, shows the difference that the wrong count
  makes. For a wrong token count, the report names the arithmetic that most likely produced it, such
  as "cache reads and writes left out".

## How to fix it

Each class in the output tells you what you can do:

1. If a class has a `Today:` line, follow it. It is a change that you can make now, such as an
   environment variable or a client option.
2. If a class has a `Fixed upstream:` line, the library has fixed it. Upgrade to the version that
   the line names. In the `--json` output, this is an upstream issue with the status `fixed-upstream`.
3. If not, open the `Tracked at` link. It shows whether a newer release of the library has the fix.
4. Run `uvx spantruth doctor` again after the change.

## Read a finding

[The report](https://plexito-de.github.io/spantruth/) shows every library, every scenario, and every
finding with the rule it applies. It is rebuilt every night, at pinned versions and at the latest
releases.

Each finding on the report shows the expected value, the reported value, the attribute it was
read from, and the rule it applies, with a link wherever a spec states it. It also shows the spans
the library exported and the command that reproduces it, and it has its own link, so an upstream
issue can point at it.

Each cell has one verdict, such as `pass`, `wrong` or `lost`. [How it works](how-it-works.md#verdicts)
lists all six.

## Know when a fix lands

Every night spantruth runs the latest release of each library. When a new release changes a result, it
appears on [the changes page](https://plexito-de.github.io/spantruth/changes.html) and in
[the feed](https://plexito-de.github.io/spantruth/feed.xml).

## Let a coding agent check it

The [spantruth skill](../skills/spantruth/SKILL.md) tells a coding agent when to run `doctor` and how to
read its JSON. This repository is a Claude Code plugin marketplace that ships it. In Claude Code, run:

```text
/plugin marketplace add Plexito-de/spantruth
/plugin install spantruth@spantruth
```

Claude Code names a plugin's skills with the plugin's name as a prefix, so the skill is
`spantruth:spantruth`. Claude uses it by itself after a change to LLM tracing, and you can run it as
`/spantruth:spantruth`.

The skill runs the spantruth version that the plugin names, so a new spantruth release cannot change
what it runs until you update the plugin. For another agent, copy the skill directory into the agent's
skills directory.
