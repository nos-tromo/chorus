#!/usr/bin/env bash
# Wait for the data-plane Neo4j instance(s) to become reachable on `data-net`
# before bringing up the chorus app. Intended to be run from the chorus repo
# root via `make bootstrap`.
#
# With CHORUS_PROJECTS set (in the environment or repo-root .env), one
# instance per project is expected under the alias `neo4j-<project>`
# (ADR 0017). Otherwise single-project compat mode waits for `neo4j`.
# SERVICES overrides the derived list entirely.

set -euo pipefail

NETWORK="${NETWORK:-data-net}"
PORT="${PORT:-7687}"
TIMEOUT="${TIMEOUT:-120}"

# Pick up CHORUS_PROJECTS from .env when not already exported, matching what
# compose hands the backend via env_file.
if [[ -z "${CHORUS_PROJECTS:-}" && -f .env ]]; then
  CHORUS_PROJECTS="$(sed -n 's/^CHORUS_PROJECTS=//p' .env | tail -1)"
fi

if [[ -n "${SERVICES:-}" ]]; then
  read -r -a SERVICE_LIST <<<"$SERVICES"
elif [[ -n "${CHORUS_PROJECTS:-}" ]]; then
  SERVICE_LIST=()
  IFS=',' read -r -a PROJECTS <<<"$CHORUS_PROJECTS"
  for project in "${PROJECTS[@]}"; do
    project="$(echo "$project" | tr -d '[:space:]')"
    [[ -n "$project" ]] && SERVICE_LIST+=("neo4j-${project}")
  done
else
  SERVICE_LIST=("neo4j")
fi

if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  echo "network '$NETWORK' not found — start the inference + data-plane stacks first." >&2
  exit 1
fi

echo "waiting up to ${TIMEOUT}s for ${SERVICE_LIST[*]} on port ${PORT} (${NETWORK})..."
START=$(date +%s)
PENDING=("${SERVICE_LIST[@]}")
while :; do
  STILL_PENDING=()
  for service in "${PENDING[@]}"; do
    if docker run --rm --network "$NETWORK" busybox:1.37 \
        sh -c "nc -z -w 2 ${service} ${PORT}" >/dev/null 2>&1; then
      echo "${service} reachable."
    else
      STILL_PENDING+=("$service")
    fi
  done
  PENDING=("${STILL_PENDING[@]+"${STILL_PENDING[@]}"}")
  if (( ${#PENDING[@]} == 0 )); then
    echo "data-plane reachable."
    exit 0
  fi
  NOW=$(date +%s)
  if (( NOW - START > TIMEOUT )); then
    echo "timed out waiting for: ${PENDING[*]} (port ${PORT})" >&2
    exit 1
  fi
  sleep 2
done
