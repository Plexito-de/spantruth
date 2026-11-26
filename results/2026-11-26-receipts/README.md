# Receipts: wrong numbers in three backends, 2026-11-26

What Arize Phoenix, MLflow and Jaeger show when an instrumentation library reports a wrong span. One
receipt for each bug class that changes a number a person reads: a ghost span (ST01), a double span
(ST02), cache-blind tokens (ST03), phantom tokens (ST04), vanishing tokens (ST05) and a false OK (ST07).
The class names are in `src/spantruth/bugclasses.py`.

Each receipt is one real spantruth cell: `spantruth.driver instrumented`, in the subject's own environment,
against spantruth's fake provider, with the library versions that `subjects/` pins. The only change from
`spantruth run` is the OTLP URL. The driver sends to a tee on loopback (`scripts/cell.py`). The tee keeps
each request for the grader, and sends the same bytes to the backend. So the graded spans and the spans in
the backend are the same spans.

All three backends ran on 127.0.0.1, with no docker, no account and no cloud, from fresh data directories.
Every request off loopback was refused: the subjects and the backends had a dead proxy, and the browser that
took the screenshots aborted each request to another host. Only the Phoenix pages tried any: `api.github.com`, `fonts.googleapis.com` and `pypi.org` (`screens/blocked.json`).

## Versions

| What | Version |
| --- | --- |
| Arize Phoenix | 22.1.0 (`arize-phoenix` from PyPI, `scripts/freeze/venv-phoenix.txt`) |
| MLflow | 3.18.0 (`mlflow` from PyPI, `scripts/freeze/venv-mlflow.txt`) |
| Jaeger | 2.23.0, the `jaeger-2.23.0-linux-amd64.tar.gz` release binary, memory storage, `scripts/jaeger.yaml` |
| Browser | Chromium 159.0.8154.6 from Python Playwright 1.65.2 |
| spantruth | this commit, subject environments as pinned in `subjects/` |

The Jaeger binary matched the release's `jaeger-2.23.0-linux-amd64.sha256sum.txt`
(`c6cea66870096a0af23cce386f35bff5540c21786d71ac6ced3060b754ce4423`).

## The receipts

The wire value is the scenario's truth: what the fake provider sent, as spantruth grades it. Input
tokens include cached tokens, as the OpenTelemetry GenAI conventions count them. The list cost is that
truth at the rates in `prices.toml` (Claude Sonnet 5.5: $2 input, $0.10 cache read, $2.50 cache write,
$10 output per million; GPT-5 mini: $0.25 input, $2 output). Phoenix's own prices for
`claude-sonnet-5-5` gave the same input and output rates in every cell. All numbers are in
`receipts.json`, built by `scripts/summarize.py` from `cells/` and `backends.json`.

| Class | Library | Scenario | Wire | Phoenix | MLflow | Jaeger |
| --- | --- | --- | --- | --- | --- | --- |
| ST01 Ghost span | OpenLLMetry `opentelemetry-instrumentation-anthropic` 0.62.4, anthropic 2.4.0 | `anthropic/stream-early-exit` | 1,913 input tokens in `message_start` before the app left the stream | Only the app's span. 0 tokens, Total Cost `$0` | One trace, State OK, Tokens `-` | One span, the app's |
| ST02 Double span | OpenTelemetry GenAI `opentelemetry-instrumentation-genai-anthropic` 1.3b0, anthropic 2.4.0 with its own tracing on | `anthropic/basic` | 1,187 in, 263 out: 1,450 tokens, $0.005004 | Two LLM spans of 1,450 tokens each. Trace cost `$0.0100`, project cost 0.010008. The project's `tokenCountTotal` is 1,450 | Tokens 1450, correct | `GenAI calls: 2` |
| ST03 Cache-blind tokens | OpenLIT 1.45.0, anthropic 0.125.0 | `anthropic/cache-hit` | 5,802 in (1,187 uncached, 4,096 cache read, 519 cache write), 377 out: 6,179 tokens, $0.0078511 | 1,564 tokens. Trace cost `$0.0055` (0.0054771): input `$0.0017`, output `$0.0038` | Tokens 1564 | Input 1,187, Output 377, Cached (read) 4,096, Cached (write) 519 |
| ST04 Phantom tokens | OpenLLMetry `opentelemetry-instrumentation-anthropic` 0.62.4, anthropic 2.4.0 | `anthropic/stream` | 2,311 in, 419 out: 2,730 tokens, $0.008812 | 2,731 tokens, 420 completion. Cost 0.008822, shown as `$0.0088` | Tokens 2731 | Input 2,311, Output 420 |
| ST05 Vanishing tokens | OpenLLMetry `opentelemetry-instrumentation-openai` 0.62.4, openai 3.42.1 | `openai/stream-helper` | 2,137 in, 313 out: 2,450 tokens, $0.00116025 | The LLM span, 0 tokens, Total Cost `$0` | Tokens `-` | The LLM span, no token line |
| ST07 False OK | OpenLLMetry `opentelemetry-instrumentation-openai` 0.62.4, openai 3.42.1 | `openai/stream-dropped` | The connection drops mid-stream. The app gets `openai.APIConnectionError` | `openai.chat` OK | State OK | No error mark. The API returns `otel.status_code=OK` |

Notes on what each backend did with the span it got:

- **ST02:** Phoenix adds the cost of both spans, and its trace and project cost are twice the list cost.
  Its project field `tokenCountTotal` stays at one count, as `results/2026-11-06-double-spans/` found for
  nested spans. MLflow showed 1,450 tokens, the right count, from the same two spans. Jaeger counts two
  GenAI calls for one call.
- **ST03:** OpenLIT puts the uncached count, 1,187, in `gen_ai.usage.input_tokens`, which the conventions
  define to include cached tokens. Phoenix's input cost, 0.0017071, equals 4,096 cache reads at $0.10 plus
  519 cache writes at $2.50 per million, with nothing for the 1,187 uncached tokens. The cost is 30% under
  the list cost. MLflow's `mlflow.trace.tokenUsage` keeps the three counts apart, and its Tokens column shows
  1,564 of 6,179.
- **ST04:** one token too many. The cost differs in the fifth decimal, and the token count is what moves.
- **ST01 and ST05:** the backend cannot show what never arrived. A trace or span without numbers looks
  like a call that cost nothing.
- **ST07:** all three show a dropped stream as a success, so any error rate built on span status leaves
  it out.

The default columns of MLflow's traces table showed no cost, and other views were not searched for one, so
only its token counts are compared. Jaeger
shows the span's attributes as they are, and adds no totals except the GenAI call count.

## Files

- `receipts.json`: the summary above, per receipt.
- `cells/rc-<receipt>-<backend>.json`: each cell as `scripts/cell.py` wrote it. Graded checks, every span
  that the tee forwarded, the requests that the fake provider saw, the backend's HTTP status for each
  forwarded request, and the exception the app got.
- `backends.json`: what each backend's API returned, by `scripts/read_backends.py`. Phoenix GraphQL,
  MLflow `traces/search`, Jaeger `/api/traces/<id>`.
- `screens/`: full screenshots. `<receipt>-phoenix-trace` selects the library's span,
  `<receipt>-phoenix-cost` adds the Total Cost tooltip, `<receipt>-mlflow-traces` is the experiment's
  traces table, `<receipt>-jaeger-genai` and `-details` are the span's two tabs. `scripts/jobs-rc.json`
  holds the URLs and the steps.

## Reproduce

Linux, [uv](https://docs.astral.sh/uv/), Python 3.12, a spantruth clone at this commit. Nothing here needs a
key. Paths assume the repository root as the working directory and an empty `WORK`.

```bash
export WORK="$HOME/receipts-work"
export XDG_CACHE_HOME="$WORK/xdg"; mkdir -p "$WORK"
RES=results/2026-11-26-receipts

# 1. Backends. Phoenix and MLflow from PyPI, Jaeger from its GitHub release.
for b in phoenix mlflow; do
  uv venv -q --python 3.12 "$WORK/venv-$b"
  uv pip install -q --python "$WORK/venv-$b/bin/python" -r "$RES/scripts/freeze/venv-$b.txt"
done
(cd "$WORK" && gh release download v2.23.0 -R jaegertracing/jaeger \
  -p 'jaeger-2.23.0-linux-amd64.tar.gz' -p 'jaeger-2.23.0-linux-amd64.sha256sum.txt' \
  && tar xzf jaeger-2.23.0-linux-amd64.tar.gz && sha256sum -c jaeger-2.23.0-linux-amd64.sha256sum.txt)

# 2. Start each in its own shell. Ports: Phoenix 16106, MLflow 15100, Jaeger 14418 (OTLP) and 16786 (UI).
WORK=$WORK bash $RES/scripts/start.sh phoenix
WORK=$WORK bash $RES/scripts/start.sh mlflow
WORK=$WORK bash $RES/scripts/start.sh jaeger

# 3. Run the six receipts on the three backends. The first run builds the subject environments.
bash $RES/scripts/run.sh rc "$WORK/cells"

# 4. Read the backends, summarize, and take the screenshots.
python3 -I $RES/scripts/read_backends.py rc- "$WORK/cells" "$WORK/backends.json"
python3 -I $RES/scripts/summarize.py rc "$WORK/cells" "$WORK/backends.json" prices.toml "$WORK/receipts.json"
python3 -I $RES/scripts/make_jobs.py rc "$WORK/cells" "$WORK/backends.json" "$WORK/jobs.json"
uvx --with playwright==1.65.2 python -I $RES/scripts/shoot.py "$WORK/jobs.json" "$WORK/screens"
uvx --with pillow python -I $RES/scripts/crop.py "$WORK/screens" "$WORK/crops"
```

One receipt alone, such as the double span on Phoenix:

```bash
uv run python results/2026-11-26-receipts/scripts/cell.py otel-official-sdk-on anthropic/basic phoenix my-double /tmp
```

The same six cells ran once before, on other data directories, under the tag `r1`. Every backend number
was the same. Those files are not kept. Project IDs, trace IDs, ports and timings differ on each run.

## Not measured

- Langfuse. Its self-hosted version needs Postgres, ClickHouse, Redis and S3-compatible storage, and its
  documented setups are container images. Without docker it was not run.
- Grafana Tempo. It ships as a single binary, but it needs Grafana to show a trace. It was not run in this
  round.
- Opik, which round 2 of `results/2026-11-06-double-spans/` measured with podman. No podman was used here.
- False alarm (ST08), hidden reasoning (ST06), silent stop (ST09) and the observer effect (ST10). They do
  not change a token count or a cost.
- Other libraries and other scenarios with the same classes, async calls, a real provider, and each
  backend's own SDK or integration. The spans came over OTLP only.
- Each backend's dashboards beyond the pages in `screens/`, and how each backend aggregates over many calls.
