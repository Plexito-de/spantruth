#!/usr/bin/env bash
# Start one backend on loopback, in the foreground, with its data under $WORK. No docker.
# Usage: WORK=<dir> start.sh phoenix|mlflow|jaeger
# Expects $WORK/venv-phoenix, $WORK/venv-mlflow and $WORK/jaeger-2.23.0-linux-amd64/jaeger.
set -euo pipefail
: "${WORK:?set WORK}"
HERE="$(cd "$(dirname "$0")" && pwd)"
# A dead proxy for anything that honors the proxy variables. Loopback stays reachable.
export HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 NO_PROXY=127.0.0.1,localhost
case "${1:?backend}" in
  phoenix)
    export PHOENIX_WORKING_DIR="$WORK/data/phoenix" PHOENIX_HOST=127.0.0.1 PHOENIX_PORT=16106 \
      PHOENIX_GRPC_PORT=14417 PHOENIX_TELEMETRY_ENABLED=false
    mkdir -p "$PHOENIX_WORKING_DIR"
    exec "$WORK/venv-phoenix/bin/phoenix" serve ;;
  mlflow)
    mkdir -p "$WORK/data/mlflow"
    export MLFLOW_DISABLE_TELEMETRY=true DO_NOT_TRACK=1
    exec "$WORK/venv-mlflow/bin/mlflow" server --host 127.0.0.1 --port 15100 \
      --backend-store-uri "sqlite:///$WORK/data/mlflow/mlflow.db" \
      --default-artifact-root "$WORK/data/mlflow/artifacts" ;;
  jaeger)
    exec "$WORK/jaeger-2.23.0-linux-amd64/jaeger" --config "$HERE/jaeger.yaml" ;;
  *) echo "phoenix, mlflow or jaeger" >&2; exit 2 ;;
esac
