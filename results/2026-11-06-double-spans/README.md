# Double spans on two backends, 2026-11-06

What two LLM observability backends report for one Anthropic call, when the SDK's own tracing and an
instrumentation library both write a span.

Each configuration made one sync, non-streaming `messages.create` for `claude-sonnet-4-5-20250929` against a local stub that returned 1000
input tokens and 200 output tokens. The stub logged one HTTP request per run, so no call was sent twice. All
numbers come from each backend's API. They are in `phoenix-r1.json`, `phoenix-r2.json` and `opik-o1.json`.

| Name in the files | Meaning |
| --- | --- |
| `A-` | SDK tracing on, the default |
| `B-` | `ANTHROPIC_OPEN_TELEMETRY=false` |
| `C-` | SDK tracing alone, with no library |
| `oi` | openinference-instrumentation-anthropic 3.3.0, anthropic 2.4.0 |
| `oi310` | openinference-instrumentation-anthropic 3.1.0, anthropic 2.4.0 |
| `ll` | opentelemetry-instrumentation-anthropic (OpenLLMetry) 0.62.4, anthropic 2.4.0 |
| `otel` | opentelemetry-instrumentation-genai-anthropic 1.3b0, anthropic 2.4.0 |
| `*latest` | openinference 3.3.1, OpenLLMetry 0.63.0, GenAI 1.3b0, with anthropic 2.6.0 |

Backends: Arize Phoenix 21.0.0 and Opik 2.2.143, both self-hosted, with telemetry off. `scripts/` holds what
ran. Each `run_*.sh` expects one virtual environment per library (`venv-oi`, `venv-ll` and so on), the stub on
port 18099, and the backend on the port the script names.

Each call has a global tracer provider and no parent span in the app. Both backends price the model at $3 and
$15 per million tokens, which matches [Anthropic's list price](https://platform.claude.com/docs/en/about-claude/pricing)
for Claude Sonnet 4.5 on 2026-11-06.

Not measured: real API calls, streaming, async calls, retries, the backends' web UI, each library's quickstart,
and any other backend.

## Round 2: web pages, parent span, streaming, cache tokens, latest versions

Same day, same backends, new run tag `x2`. The older files above are unchanged. This round measures the things
that the "Not measured" line above lists, except real API calls, async calls, retries, each library's quickstart
and other backends. All backend numbers are in `phoenix-x2.json` and `opik-x2.json`. `spans-x2-*.jsonl` holds every
exported span with its attributes. `calls-x2.jsonl` shows one stub request for each of the 94 calls.
`stub_hits-x2.jsonl` has 96 lines, because two smoke tests under the tag `x2t` ran first. No `x2t` project is in
the JSON files. `ui-readings.json` holds what each web page displayed, and `screens/` holds the screenshots.

Project names are `x2-<variant>-<config>`:

| Variant | Call |
| --- | --- |
| `base` | `messages.create`, as in round 1 |
| `parent` | the same, inside an app span named `app.handle_request` with no token counts |
| `streamc` | `messages.create(stream=True)`, iterated to the end |
| `streamh` | `messages.stream(...)`, iterated to the end, then `get_final_message()` |
| `cache` | `messages.create`, usage 1000 input, 4000 cache read, 800 cache write, 200 output |
| `latest` | Opik only: `B-oilatest`, `B-lllatest`, `B-otellatest`, `C-sdk26` |

Configs are as in the table above. `base`, `parent`, `streamc`, `streamh` and `cache` each ran `A-` and `B-` for `ll`,
`otel`, `oi310` and `oi`, and `C-sdk`, on both backends.

The stream events follow `src/spantruth/wire_anthropic.py`: `message_start` carries `output_tokens: 1` and
`message_delta` carries `output_tokens: 200`. The variant where `message_delta` repeats the input and cache counts
was not run, and a stream that fails or ends early was not run.

List price of the `cache` call, for Sonnet 4.5 at $3 input, $15 output, $0.30 cache read, $3.75 cache write per
million tokens: $0.003 + $0.003 + $0.0012 + $0.003 = **$0.0102**, 6,000 tokens. The two cache rates are 0.1x and 1.25x of
the input price, from Anthropic's caching documentation as bundled with the Claude API skill. The pricing page itself
was not fetched in this round, because the run stayed on loopback. Both backends price `cache_creation` tokens at
exactly this rate.

### Reproduce

Linux, podman 4.9.3, [uv](https://docs.astral.sh/uv/), Python 3.12. Nothing here needs a key. All paths below assume
this directory as `RES` and an empty working directory as `WORK`.

```bash
export RES="$PWD" WORK="$HOME/double-spans-work"; mkdir -p "$WORK/out"; cd "$WORK"

# 1. Environments. Each file in scripts/freeze/ is the full `uv pip freeze` of one venv.
for f in "$RES"/scripts/freeze/venv-*.txt; do
  n=$(basename "$f" .txt); uv venv -q --python 3.12 "$n"
  uv pip install -q --python "$n/bin/python" -r "$f"
done
```

`venv-oi310` was rebuilt this way into an empty directory with `uv pip install --offline`, and its freeze matched
the file exactly. The other eight were not rebuilt.

```bash
# 2. Opik's compose files: deployment/docker-compose/ from comet-ml/opik.
#    Check them against scripts/round2/opik-compose-upstream.sha256 (run it inside that directory).
mkdir -p out/opik-compose-x2 && cp -a /path/to/opik/deployment/docker-compose/. out/opik-compose-x2/
(cd out/opik-compose-x2 && patch -p4 < "$RES/scripts/round2/opik-compose.patch")
```

The patch was checked against the upstream files. The source commit of those files was not recorded, so the hashes are
the reference: use the checkout whose files match them. What it changes:

- Short image names become `docker.io/...`, for podman.
- `pull_policy: always` becomes `never`, so that nothing is pulled at start. Pull the images in
  `scripts/round2/opik-images.txt` first, and compare their digests with that file.
- The `:type=volume,source=~/opik/...` volume suffixes are removed, for podman-compose.
- The backend waits for ClickHouse before it migrates.
- The backend's `3003` port is no longer published, and the frontend publishes on `127.0.0.1:15173` only.
- ClickHouse listens on `0.0.0.0` inside its container.
- nginx uses the podman network's gateway as its resolver instead of `127.0.0.11`.
  `start_opik.sh` writes the actual gateway address on each start.

```bash
# 3. Start everything. Phoenix: loopback, telemetry off. Opik: usage report off, TOGGLE_OPIK_AI off,
#    on a podman network created with --internal, so containers have no route off the host.
"$RES/scripts/round2/start_phoenix.sh" > out/phoenix-x2.log 2>&1 &
"$RES/scripts/round2/start_opik.sh"     # wait until the frontend container is healthy
venv-oi/bin/python -I "$RES/scripts/round2/stub.py" 18099 out/stub_hits-x2.jsonl &
venv-oi/bin/python -I "$RES/scripts/round2/stub.py" 18098 out/stub_hits-x2.jsonl 4000 800 &

# 4. Run and read. Each call is its own process and its own project. A dead proxy is set for the clients.
"$RES/scripts/round2/run.sh" phoenix x2
"$RES/scripts/round2/run.sh" opik x2
sleep 15   # this run waited 15 s before reading Opik
venv-oi/bin/python -I "$RES/scripts/round2/read_phoenix.py" x2 out/phoenix-x2.json
venv-oi/bin/python -I "$RES/scripts/round2/read_opik.py" x2 out/opik-x2.json
python3 "$RES/scripts/round2/summarize.py" out/phoenix-x2.json out/opik-x2.json      # one table per variant

# 5. Stop. Volumes stay; add -v to remove them.
(cd out/opik-compose-x2 && OPIK_VERSION=2.2.143 "$WORK/venv-compose/bin/podman-compose" -p receiptopik \
  -f docker-compose.yaml --profile opik down)
podman network rm receiptopik_default
kill %1 %2 %3
```

`scripts/start_phoenix.sh` and `scripts/start_opik.sh` are round 1's versions, added here because they were missing.
The `scripts/round2/` versions take `WORK` from the environment.

Web pages: open `http://127.0.0.1:16006/projects` and `http://127.0.0.1:15173/default/projects`. The screenshots come from
a browser that aborted every request off loopback. Left unblocked, Phoenix's page would request `fonts.googleapis.com`,
`pypi.org` and `api.github.com`, and Opik's page `fonts.googleapis.com` and `api.github.com`. Opik's welcome dialog was
closed with "Skip and go to Opik", and no form was submitted. Phoenix's exact cost is a tooltip on the cost cell, and
Opik's exact cost a tooltip on the cost cell or tile.

Round 2 ran on a Phoenix data directory and Opik volumes that already held the round 1 projects and Opik's generated demo
projects. A fresh start gives different project IDs, and the same numbers are expected but were not run that way.

### Results

Cost in dollars. "P" is Phoenix `tokenCountTotal` and `costSummary.total.cost`. "O" is Opik's sum of
`usage.total_tokens` and of `total_estimated_cost` over the project's traces. `-` means Opik reports no cost, and for
OpenInference spans Opik's `usage` has the keys `prompt`, `completion` and `total` instead of `*_tokens`.

| Config | `parent` P | `parent` O | `streamc` P | `streamc` O | `streamh` P | `streamh` O | `cache` P | `cache` O |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `A-ll` | 2,400, 0.012 | 2,400, 0.012 | 2,401, 0.012015 | 2,401, 0.012015 | 2,401, 0.012015 | 2,401, 0.012015 | 12,000, 0.0198 | 12,000, 0.0198 |
| `A-otel` | 1,200, 0.012 | 2,400, 0.012 | 1,200, 0.012 | 2,400, 0.012 | 1,200, 0.012 | 2,400, 0.012 | 6,000, 0.0192 | 12,000, 0.0192 |
| `A-oi310` | 1,200, 0.012 | 1,200, 0.006 | 1,200, 0.012 | 1,200, 0.006 | 2,400, 0.012 | 1,200, 0.006 | 6,000, 0.0198 | 6,000, 0.0096 |
| `A-oi` | 1,200, 0.006 | -, - | 1,200, 0.006 | -, - | 1,200, 0.006 | -, - | 6,000, 0.0102 | -, - |
| `B-ll` | 1,200, 0.006 | 1,200, 0.006 | 1,201, 0.006015 | 1,201, 0.006015 | 1,201, 0.006015 | 1,201, 0.006015 | 6,000, 0.0102 | 6,000, 0.0102 |
| `B-otel` | 1,200, 0.006 | 1,200, 0.006 | 1,200, 0.006 | 1,200, 0.006 | 1,200, 0.006 | 1,200, 0.006 | 6,000, 0.0096 | 6,000, 0.0096 |
| `C-sdk` | 1,200, 0.006 | 1,200, 0.006 | 1,200, 0.006 | 1,200, 0.006 | 1,200, 0.006 | 1,200, 0.006 | 6,000, 0.0096 | 6,000, 0.0096 |

`B-oi310` and `B-oi` are in the JSON: Phoenix is correct in every variant, and Opik shows no cost.

Opik, `latest`: `B-lllatest` and `B-otellatest` are 1,200 and $0.006, `B-oilatest` has no cost, and `C-sdk26` is 1,200 and
$0.006.

Causes that the span tee shows:

- **201 output tokens:** OpenLLMetry 0.62.4 records 201 on a stream, the 1 from `message_start` plus the 200 from `message_delta`.
  The client saw 200. 0.63.0 was not run with a stream.
- **$0.0096:** the SDK and the GenAI instrumentation write `gen_ai.usage.cache_write.input_tokens`. Neither backend reads that
  name as a cache write, so the 800 tokens cost $3 instead of $3.75 per million. Phoenix shows `cacheWrite` as null there.
  OpenLLMetry's `gen_ai.usage.cache_creation.input_tokens` is priced correctly.
- **Two traces for `A-oi310` on `streamh`:** the SDK's span has no parent and its own trace ID, next to OpenInference's `messages.stream` span.
  On `streamc` the SDK's span is a child of OpenInference's `messages.create` span. The tee shows this, not the reason for it.
- **Parent:** in all three doubled configs both spans share the app's trace. OpenLLMetry's two spans are siblings under the app span.
  The GenAI and OpenInference spans nest the SDK's span.
- **Phoenix `tokenCountTotal`** stays at one count when the second span is nested, and doubles when the spans are not nested.
  The traces table and the Metrics tab use other totals, so the pages disagree with it: see `ui-readings.json`.
