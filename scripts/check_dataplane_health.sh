#!/usr/bin/env bash
# Wait for the data-plane Neo4j (`neo4j-chorus`) to become reachable on
# `inference-net` before bringing up the chorus app. Intended to be run
# from the chorus repo root via `make bootstrap`.

set -euo pipefail

NETWORK="${NETWORK:-inference-net}"
SERVICE="${SERVICE:-neo4j-chorus}"
PORT="${PORT:-7687}"
TIMEOUT="${TIMEOUT:-120}"

if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  echo "network '$NETWORK' not found — start the inference + data-plane stacks first." >&2
  exit 1
fi

echo "waiting up to ${TIMEOUT}s for ${SERVICE}:${PORT} on ${NETWORK}..."
START=$(date +%s)
while :; do
  if docker run --rm --network "$NETWORK" busybox:1.37 \
      sh -c "nc -z -w 2 ${SERVICE} ${PORT}" >/dev/null 2>&1; then
    echo "data-plane reachable."
    exit 0
  fi
  NOW=$(date +%s)
  if (( NOW - START > TIMEOUT )); then
    echo "timed out waiting for ${SERVICE}:${PORT}" >&2
    exit 1
  fi
  sleep 2
done
