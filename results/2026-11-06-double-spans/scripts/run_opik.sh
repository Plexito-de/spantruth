#!/usr/bin/env bash
# One call per config against local Opik, each config in its own Opik project. Arg 1: tag.
BASE="$(cd "$(dirname "$0")/.." && pwd)"; cd "$BASE"; TAG="${1:-o1}"
OTLP=http://127.0.0.1:15173/api/v1/private/otel/v1/traces; STUB=http://127.0.0.1:18099
export HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 NO_PROXY=127.0.0.1,localhost PYTHONWARNINGS=ignore
run() { local proj="$TAG-$1"; export OTEL_EXPORTER_OTLP_HEADERS="projectName=$proj"
  if [ "$4" = off ]; then ANTHROPIC_OPEN_TELEMETRY=false venv-$3/bin/python -I scripts/call.py "$2" "$proj" $OTLP $STUB out/spans-$TAG.jsonl
  else env -u ANTHROPIC_OPEN_TELEMETRY venv-$3/bin/python -I scripts/call.py "$2" "$proj" $OTLP $STUB out/spans-$TAG.jsonl; fi; }
run A-oi oi oi on;       run B-oi oi oi off;       run C-sdk none oi on
run A-ll ll ll on;       run B-ll ll ll off
run A-otel otel otel on; run B-otel otel otel off
run A-oi310 oi oi310 on; run B-oi310 oi oi310 off
run A-oilatest oi oilatest on; run A-lllatest ll lllatest on; run A-otellatest otel otellatest on; run C-sdk26 none oilatest on
