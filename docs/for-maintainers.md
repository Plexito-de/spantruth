# spantruth for library maintainers

You maintain an LLM instrumentation library, or a wrapper that your company uses. This page tells you
how to run spantruth against your own code, how to make it a CI gate that fails only on a change, and
how to show the result.

## Run it

You need [uv](https://docs.astral.sh/uv/). No clone, no API keys:

```sh
uvx spantruth list                                      # scenarios and subjects
uvx spantruth run --subject openllmetry                 # one library, every scenario
uvx spantruth run --out results.json                    # everything, about 3 minutes
uvx spantruth report results.json --html report.html
```

Each subject gets its own virtual environment under `~/.cache/spantruth/venvs`
(`$XDG_CACHE_HOME/spantruth` when that is set). A run writes nothing into the directory you run it
in: without `--out`, the results go to `~/.cache/spantruth/results/latest.json`. `--latest` unpins
the library's own packages. The run behind the table in the README is committed as
[`results/2026-11-03-pinned.json`](../results/2026-11-03-pinned.json).

To work on spantruth itself, use a clone: `git clone https://github.com/Plexito-de/spantruth.git`,
then `uv sync` and `uv run spantruth list` in it.

## Test your own library, and a fix before it merges

`--with` replaces one package of a subject with a local checkout, a pull request's head, a
branch, or a version. A checkout is named by its `pyproject.toml`, and the results record what
each value resolved to.

```sh
uvx spantruth run --subject openllmetry --with ./packages/opentelemetry-instrumentation-anthropic
uvx spantruth run --subject openllmetry --with \
  "opentelemetry-instrumentation-anthropic @ git+https://github.com/traceloop/openllmetry@refs/pull/4377/head#subdirectory=packages/opentelemetry-instrumentation-anthropic"
```

A subject can live in your own repository. Copy the two files of a subject in
[`subjects/`](../subjects/), `subject.toml` and `instrument.py`, into a directory and run it by its
path. A value with a slash is a path, and a value without one is a built-in subject:

```sh
uvx spantruth run --subject ./spantruth
```

[Test your wrapper in five minutes](test-your-wrapper.md) shows the two files for a small in-house
wrapper, step by step.

## Fail CI on a regression, not on a known bug

Record the checks that your library fails today, commit the file, and compare every later run
with it:

```sh
uvx spantruth run --subject ./spantruth --write-expect spantruth-expected.toml
uvx spantruth run --subject ./spantruth --expect spantruth-expected.toml
```

| Exit code | Meaning |
| --- | --- |
| 0 | Every cell ran, and the failing checks equal the expectation file. |
| 1 | The failing checks differ from the file: a new failure, a fix, or a changed verdict. The output names each one. |
| 2 | Usage error, for example an unknown scenario in the expectation file. |
| 3 | spantruth could not build an environment or run a cell. This is not a verdict on the library. |

A fix fails the comparison too, so the file always says what the library fails today: record the
fix with `--write-expect`. `--summary "$GITHUB_STEP_SUMMARY"` adds the report to a GitHub Actions
job summary.

An expectation file names scenario IDs, such as `anthropic/stream`, and check IDs, such as
`output_tokens`. These IDs never change meaning, so a new spantruth release cannot silently change
what your file says. A new meaning gets a new ID.

## Run it in GitHub Actions

```yaml
jobs:
  spantruth:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write                           # only for comment: "true"
    steps:
      - uses: actions/checkout@v5
      - uses: Plexito-de/spantruth@v0.6.0
        with:
          subject: ./spantruth                       # or a built-in subject, such as openllmetry
          with: ./packages/my-instrumentation        # your checkout, one per line
          expect: spantruth-expected.toml            # optional: fail only on a change
          comment: "true"                            # optional: one comment on each pull request
```

The step installs spantruth from its own ref, so a new release cannot change your results until
you move the tag. It writes the matrix, the findings and the differences to the job summary,
uploads the results and the HTML report as an artifact, and fails with the exit code of the run.
The `exit-code` output tells a difference (1) from an incomplete run (3). It runs on Linux and
macOS runners.

The other inputs are in [`action.yml`](../action.yml): `scenario` (scenario IDs, one per line),
`latest` (unpin the tracked packages and the provider SDKs), `artifact-name` and `token`. The
`results` output is the path of the results document.

### The pull request comment

With `comment: "true"` and `pull-requests: write`, each pull request gets one comment that names the
checks that changed, and later runs update the same comment. A pull request from a fork has a read-only
token, so the step warns and the job goes on.

## Show the result

The nightly run against the latest releases publishes a [shields.io](https://shields.io/badges/endpoint-badge)
endpoint per library, at `https://plexito-de.github.io/spantruth/badges/<subject>.json`:

```markdown
[![spantruth](https://img.shields.io/endpoint?url=https://plexito-de.github.io/spantruth/badges/otel-official.json)](https://plexito-de.github.io/spantruth/latest.html)
```

## Follow new releases

Every night spantruth runs the latest release of each library. When a new release changes a result, it
appears on [the changes page](https://plexito-de.github.io/spantruth/changes.html) and in
[the feed](https://plexito-de.github.io/spantruth/feed.xml).

## Is the adapter for your library fair?

Each subject in [`subjects/`](../subjects/) attaches its library with the setup that the library's own
documentation shows first. Review `subjects/<name>/instrument.py` for your library. If it does not use
your documented setup, the results are not fair, and we want to know.

If a cell is unfair, [tell us](https://github.com/Plexito-de/spantruth/issues/new?template=dispute.yml).
We decide every dispute on whether the cited rule says what the check claims. [How it
works](how-it-works.md) explains the method, the controls and the verdicts.
