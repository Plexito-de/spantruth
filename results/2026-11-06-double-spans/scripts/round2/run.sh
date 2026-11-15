#!/usr/bin/env bash
# Round 2 matrix. One process and one call per config, each config in its own project on the backend.
# Usage: run.sh <backend: phoenix|opik> [tag] ; WORK=<dir with venv-* and out/> ; stubs on 18099 (no cache) and 18098 (cache).
HERE="$(cd "$(dirname "$0")" && pwd)"; cd "${WORK:?set WORK}"; BACK="$1"; TAG="${2:-x2}"
STUB=http://127.0.0.1:18099; CACHE=http://127.0.0.1:18098
case "$BACK" in
  phoenix) OTLP=http://127.0.0.1:16006/v1/traces;;
  opik)    OTLP=http://127.0.0.1:15173/api/v1/private/otel/v1/traces;;
  *) echo "backend?"; exit 2;;
esac
export HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 NO_PROXY=127.0.0.1,localhost PYTHONWARNINGS=ignore
HITS=out/stub_hits-$TAG.jsonl; touch "$HITS"
run() { # variant config lib venv sdk(on|off) stub mode [--parent]
  local proj="$TAG-$1-$2" before after sdk=()
  [ "$BACK" = opik ] && export OTEL_EXPORTER_OTLP_HEADERS="projectName=$proj"
  [ "$5" = off ] && sdk=(ANTHROPIC_OPEN_TELEMETRY=false) || sdk=(-u ANTHROPIC_OPEN_TELEMETRY)
  before=$(wc -l < "$HITS")
  env "${sdk[@]}" "venv-$4/bin/python" -I "$HERE/call.py" "$3" "$proj" "$OTLP" "$6" "out/spans-$TAG-$BACK.jsonl" "$7" ${8:-} || echo "FAILED $proj"
  after=$(wc -l < "$HITS")
  echo "{\"backend\":\"$BACK\",\"project\":\"$proj\",\"stub_requests\":$((after-before))}" >> "out/calls-$TAG.jsonl"
}
matrix() { # variant stub mode [--parent]
  run "$1" A-ll   ll   ll    on  "$2" "$3" $4
  run "$1" A-otel otel otel  on  "$2" "$3" $4
  run "$1" A-oi310 oi  oi310 on  "$2" "$3" $4
  run "$1" A-oi   oi   oi    on  "$2" "$3" $4
  run "$1" B-ll   ll   ll    off "$2" "$3" $4
  run "$1" B-otel otel otel  off "$2" "$3" $4
  run "$1" B-oi310 oi  oi310 off "$2" "$3" $4
  run "$1" B-oi   oi   oi    off "$2" "$3" $4
  run "$1" C-sdk  none oi    on  "$2" "$3" $4
}
matrix base     "$STUB"  plain
matrix parent   "$STUB"  plain --parent
matrix streamc  "$STUB"  stream-c
matrix streamh  "$STUB"  stream-h
matrix cache    "$CACHE" plain
if [ "$BACK" = opik ]; then
  run latest B-oilatest   oi   oilatest   off "$STUB" plain
  run latest B-lllatest   ll   lllatest   off "$STUB" plain
  run latest B-otellatest otel otellatest off "$STUB" plain
  run latest C-sdk26      none oilatest   on  "$STUB" plain
fi
