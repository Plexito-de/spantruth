#!/usr/bin/env bash
# Opik 2.2.143 from its own compose file, ports rewritten to loopback only, usage reporting off.
BASE="$(cd "$(dirname "$0")/.." && pwd)"; cd "$BASE/out/opik-compose"
export OPIK_VERSION=2.2.143 OPIK_USAGE_REPORT_ENABLED=false TOGGLE_OPIK_AI_ENABLED=false
exec "$BASE/venv-compose/bin/podman-compose" -p receiptopik -f docker-compose.yaml --profile opik up -d --no-build
