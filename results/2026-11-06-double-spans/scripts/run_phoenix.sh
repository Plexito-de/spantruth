#!/usr/bin/env bash
# Runs one call per config against local Phoenix. Each config gets its own project. Arg 1: run tag.
BASE="$(cd "$(dirname "$0")/.." && pwd)"; cd "$BASE"; TAG="${1:-r1}"
OTLP=http://127.0.0.1:16006/v1/traces; STUB=http://127.0.0.1:18099
export HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 NO_PROXY=127.0.0.1,localhost OPENINFERENCE_HIDE_INPUTS=false PYTHONWARNINGS=ignore
run() { # config lib venv sdk_flag
  local proj="$TAG-$1"
  if [ "$4" = off ]; then ANTHROPIC_OPEN_TELEMETRY=false venv-$3/bin/python -I scripts/call.py "$2" "$proj" $OTLP $STUB out/spans-$TAG.jsonl
  else env -u ANTHROPIC_OPEN_TELEMETRY venv-$3/bin/python -I scripts/call.py "$2" "$proj" $OTLP $STUB out/spans-$TAG.jsonl; fi
}
run A-oi oi oi on;     run B-oi oi oi off;     run C-sdk none oi on
run A-ll ll ll on;     run B-ll ll ll off;     run C-sdk-ll none ll on
run A-otel otel otel on; run B-otel otel otel off; run C-sdk-otel none otel on
