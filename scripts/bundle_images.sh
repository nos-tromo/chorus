#!/usr/bin/env bash
# Save chorus-api + chorus-ui images as a single tarball for airgap delivery.
#
# Expects the images to already be built (run `make build` first).
# Produces: dist/images.tar.gz

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION="${CHORUS_VERSION:-$(git rev-parse --short HEAD 2>/dev/null || echo dev)}"

mkdir -p dist

API_IMG="chorus-api:${VERSION}"
UI_IMG="chorus-ui:${VERSION}"

for img in "$API_IMG" "$UI_IMG"; do
  if ! docker image inspect "$img" >/dev/null 2>&1; then
    echo "image not found: $img — run 'make build' first" >&2
    exit 1
  fi
done

docker save "$API_IMG" "$UI_IMG" | gzip > dist/images.tar.gz
echo "wrote dist/images.tar.gz ($(du -h dist/images.tar.gz | cut -f1))"
