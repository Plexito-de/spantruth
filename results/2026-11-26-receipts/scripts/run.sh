#!/usr/bin/env bash
# Every receipt: one spantruth cell per backend, each in its own project.
# Usage: run.sh TAG CELLS_DIR, from the root of a spantruth clone, with the three backends up.
set -euo pipefail
TAG="${1:?tag}"; CELLS="${2:?cells dir}"; HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$CELLS"
cell() { # name subject scenario
  for backend in phoenix mlflow jaeger; do
    uv run python "$HERE/cell.py" "$2" "$3" "$backend" "$TAG-$1-$backend" "$CELLS"
  done
}
cell phantom   openllmetry          anthropic/stream
cell ghost     openllmetry          anthropic/stream-early-exit
cell falseok   openllmetry          openai/stream-dropped
cell double    otel-official-sdk-on anthropic/basic
cell cache     openlit              anthropic/cache-hit
cell vanish    openllmetry          openai/stream-helper
