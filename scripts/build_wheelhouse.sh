#!/usr/bin/env bash
# Build an offline wheelhouse for the airgapped install.
#
# Usage: ./scripts/build_wheelhouse.sh
# Produces: dist/wheelhouse.tar.gz containing every wheel + sdist from uv.lock.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mkdir -p dist wheelhouse

# Export the lock as a requirements file and download every artifact.
# --all-groups: include every dependency group (dev + frontend) so the
# wheelhouse carries Streamlit's wheels for the UI image's offline
# `uv sync --only-group frontend` on the airgapped side.
uv export --format requirements-txt --no-emit-project --frozen --no-hashes --all-groups \
    > wheelhouse/requirements.txt
uv pip download --requirement wheelhouse/requirements.txt --dest wheelhouse

tar -czf dist/wheelhouse.tar.gz -C wheelhouse .
echo "wrote dist/wheelhouse.tar.gz ($(du -h dist/wheelhouse.tar.gz | cut -f1))"
