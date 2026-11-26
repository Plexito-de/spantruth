#!/bin/sh
# Download the OpenTelemetry Collector (contrib) that every recipe in README.md was tested on.
# It checks the sha256 that the release publishes, and it installs outside the repository.
#
#   docs/remedies/fetch-otelcol.sh            # into ~/.cache/spantruth/otelcol-contrib-<version>
#   docs/remedies/fetch-otelcol.sh DIRECTORY  # into DIRECTORY
set -eu

VERSION=0.166.0
SHA256=928cb39c115d428c53211818b7e33cd8b26810b8896dc74e0bd13b8d1457c391
ARCHIVE="otelcol-contrib_${VERSION}_linux_amd64.tar.gz"
URL="https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v${VERSION}/${ARCHIVE}"
DEST="${1:-${XDG_CACHE_HOME:-$HOME/.cache}/spantruth/otelcol-contrib-${VERSION}}"

if [ -x "$DEST/otelcol-contrib" ]; then
  echo "$DEST/otelcol-contrib"
  exit 0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
curl --fail --silent --show-error --location --output "$WORK/$ARCHIVE" "$URL"
echo "$SHA256  $WORK/$ARCHIVE" | sha256sum --check --quiet
mkdir -p "$DEST"
tar -xzf "$WORK/$ARCHIVE" -C "$DEST" otelcol-contrib
echo "$DEST/otelcol-contrib"
