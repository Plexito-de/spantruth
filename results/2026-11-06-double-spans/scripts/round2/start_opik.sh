#!/usr/bin/env bash
# Opik 2.2.143 from its own compose files with loopback-only ports, usage reporting off, no image pulls,
# on a podman network created with --internal, so that containers have no route off the host.
: "${WORK:?set WORK}"; HERE="$(cd "$(dirname "$0")" && pwd)"
C="$WORK/out/opik-compose-x2"; NET=receiptopik_default
podman network exists "$NET" || podman network create --internal "$NET" >/dev/null
GW=$(podman network inspect "$NET" --format '{{range .Subnets}}{{.Gateway}}{{end}}')
[ -n "$GW" ] || { echo "no gateway on $NET"; exit 1; }
sed -i -E "s/^resolver [0-9.]+ valid/resolver $GW valid/" "$C/nginx_default_local.conf"
cd "$C"; export OPIK_VERSION=2.2.143 OPIK_USAGE_REPORT_ENABLED=false TOGGLE_OPIK_AI_ENABLED=false
exec "$WORK/venv-compose/bin/podman-compose" -p receiptopik -f docker-compose.yaml --profile opik up -d --no-build
