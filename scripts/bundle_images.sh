#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

COMPOSE="docker compose --env-file .env -f docker/compose.yaml"

# Always compute a fresh version from git so repeated bundle runs produce
# distinct tags. Uses the commit date (not the build date) for reproducibility.
# Falls back to today's date when not in a git repo.
# .chorus-version (if present) is never used as input here — it is only
# written as output for production hosts.
# To pin a specific tag, set CHORUS_VERSION_OVERRIDE in your shell before
# invoking make.
if [[ -n "${CHORUS_VERSION_OVERRIDE:-}" ]]; then
  export CHORUS_VERSION="$CHORUS_VERSION_OVERRIDE"
else
  _git_sha=$(git rev-parse --short HEAD 2>/dev/null || true)
  _git_date=$(git log -1 --format=%cs 2>/dev/null || true)
  _date="${_git_date:-$(date +%Y-%m-%d)}"
  export CHORUS_VERSION="${_date}${_git_sha:+-${_git_sha}}"
fi
echo "CHORUS_VERSION=$CHORUS_VERSION"

# Persist the version so production hosts can run 'make up' without git or the
# original build date. Copy this file alongside docker/compose.yaml.
echo "$CHORUS_VERSION" > .chorus-version

# Build locally-defined services (backend + frontend).
$COMPOSE build

# Collect the built image names so docker save can bundle them. Every
# service in this compose file is locally built (backend + frontend);
# stateful/remote images (Neo4j) live in the data-plane project, not here,
# so there is nothing to pull.
built=()
while IFS= read -r img; do
  [[ -z "$img" ]] && continue
  built+=("$img")
done < <($COMPOSE config --images)

echo "Built images: ${built[*]:-<none>}"

if (( ${#built[@]} > 0 )); then
  docker save "${built[@]}" | gzip > "chorus-built-${CHORUS_VERSION}.tar.gz"
fi

echo "Wrote: chorus-built-${CHORUS_VERSION}.tar.gz"
