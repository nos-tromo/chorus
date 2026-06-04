#!/usr/bin/env bash
# Save chorus-backend + chorus-frontend images as a versioned tarball for
# airgap delivery.
#
# Expects the images to already be built (run `make build` first).
# Produces: dist/chorus-images-<version>.tar.gz
#
# Version is YYYY-MM-DD[-<short-sha>] derived from the commit date (not the
# build date) so repeated bundle runs of the same commit produce the same
# tag. Falls back to today's date when not in a git repo. To pin a specific
# tag, set CHORUS_VERSION_OVERRIDE in your shell before invoking make.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -n "${CHORUS_VERSION_OVERRIDE:-}" ]]; then
  export CHORUS_VERSION="$CHORUS_VERSION_OVERRIDE"
else
  _git_sha=$(git rev-parse --short HEAD 2>/dev/null || true)
  _git_date=$(git log -1 --format=%cs 2>/dev/null || true)
  _date="${_git_date:-$(date +%Y-%m-%d)}"
  export CHORUS_VERSION="${_date}${_git_sha:+-${_git_sha}}"
fi
echo "CHORUS_VERSION=$CHORUS_VERSION"

# Persist the version so airgapped production hosts can run 'make up' without
# git or the original build date. Copy this file alongside docker/compose.yaml.
echo "$CHORUS_VERSION" > .chorus-version

mkdir -p dist

BACKEND_IMG="chorus-backend:${CHORUS_VERSION}"
FRONTEND_IMG="chorus-frontend:${CHORUS_VERSION}"

for img in "$BACKEND_IMG" "$FRONTEND_IMG"; do
  if ! docker image inspect "$img" >/dev/null 2>&1; then
    echo "image not found: $img — run 'make build' first" >&2
    exit 1
  fi
done

TARBALL="dist/chorus-images-${CHORUS_VERSION}.tar.gz"
docker save "$BACKEND_IMG" "$FRONTEND_IMG" | gzip > "$TARBALL"
echo "wrote $TARBALL ($(du -h "$TARBALL" | cut -f1))"
