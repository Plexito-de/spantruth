#!/usr/bin/env bash
# Extra matrix: older OpenInference without suppression, and latest of everything.
BASE="$(cd "$(dirname "$0")/.." && pwd)"; cd "$BASE"; TAG="${1:-r2}"
OTLP=${OTLP:-http://127.0.0.1:16006/v1/traces}; STUB=http://127.0.0.1:18099
export HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 NO_PROXY=127.0.0.1,localhost PYTHONWARNINGS=ignore
run() { local proj="$TAG-$1"
  if [ "$4" = off ]; then ANTHROPIC_OPEN_TELEMETRY=false venv-$3/bin/python -I scripts/call.py "$2" "$proj" $OTLP $STUB out/spans-$TAG.jsonl
  else env -u ANTHROPIC_OPEN_TELEMETRY venv-$3/bin/python -I scripts/call.py "$2" "$proj" $OTLP $STUB out/spans-$TAG.jsonl; fi; }
run A-oi310 oi oi310 on; run B-oi310 oi oi310 off
run A-oilatest oi oilatest on; run B-oilatest oi oilatest off; run C-sdk26 none oilatest on
run A-lllatest ll lllatest on; run B-lllatest ll lllatest off
run A-otellatest otel otellatest on; run B-otellatest otel otellatest off
