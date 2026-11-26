#!/usr/bin/env bash
# Purpose: replace the committed version table with the one the nightly job published, so the
#          next wheel ships it and `spantruth doctor` can answer from it offline.
# Usage:   scripts/refresh-versions.sh   (from the repository root; then review and commit versions.json)
set -euo pipefail
url="https://plexito-de.github.io/spantruth/versions.json"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
curl -fsSL -o "$tmp" "$url"
# A table that does not load is not committed.
uv run python -c '
import sys
from spantruth import history
table = history.load(sys.argv[1])
print("table of", table["created"], "with", len(table["subjects"]), "subjects")
' "$tmp"
mv "$tmp" versions.json
trap - EXIT
echo "wrote versions.json: review the diff and commit it"
