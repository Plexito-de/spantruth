#!/usr/bin/env bash
# Phoenix 21.0.0 on loopback, with telemetry off and a dead proxy for anything that honors the proxy variables.
: "${WORK:?set WORK}"
export PHOENIX_WORKING_DIR="$WORK/out/phoenix-data" PHOENIX_PORT=16006 PHOENIX_GRPC_PORT=14317 PHOENIX_HOST=127.0.0.1 PHOENIX_TELEMETRY_ENABLED=false
export HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 NO_PROXY=127.0.0.1,localhost
mkdir -p "$PHOENIX_WORKING_DIR"
exec "$WORK/venv-phoenix/bin/phoenix" serve
