#!/usr/bin/env bash
# Local Phoenix on private ports with its own SQLite dir and no telemetry.
BASE="$(cd "$(dirname "$0")/.." && pwd)"
export PHOENIX_WORKING_DIR="$BASE/out/phoenix-data" PHOENIX_PORT=16006 PHOENIX_GRPC_PORT=14317 PHOENIX_HOST=127.0.0.1 PHOENIX_TELEMETRY_ENABLED=false
mkdir -p "$PHOENIX_WORKING_DIR"
exec "$BASE/venv-phoenix/bin/phoenix" serve
