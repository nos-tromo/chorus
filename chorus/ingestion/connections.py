"""Connections (follower/following + friendship) ingestion — STUB.

This module handles the upstream's node-edge-node *edge* table — the
social graph, one relationship per row. The upstream also emits a
profile-per-row table under the same "connections" umbrella; that table
is author-profile enrichment and is handled separately by `profiles.py`
(see ADR 0006). Do not conflate the two.

The upstream `connections` (edge-table) schema is not yet pinned down.
Both entry points below raise `NotImplementedError` so the orchestrator
can skip-and-log without silently dropping rows.

Open questions to resolve before implementing (see ADR 0002):

- Exact column names emitted by the upstream system.
- Edge properties — at minimum `crawled_at`; ideally `created_at` if
  the upstream knows when the relationship started.
- Snapshot vs. delta semantics. With snapshots, chorus can detect
  removals by diffing crawl runs; with deltas lacking explicit removal
  events, chorus cannot distinguish "still active" from "no longer
  reported." This shapes retention and any "active network at time T"
  query.
- Canonical direction for `:FRIENDS_WITH` (e.g. lower UUID → higher
  UUID). Stored once per pair; queried without direction in Cypher.
- Handling of authors absent from artifact tables: create thin
  `(:Author)` nodes so multi-hop traversals find structurally complete
  paths.
- Dedupe strategy for bidirectional friendship rows emitted as two
  rows per pair.
- Self-loop filtering at ingestion.

The Neo4j schema (migrations 001–002) already provisions the
`:Author(id)` unique constraint and `:FOLLOWS / :FRIENDS_WITH` edge
indexes so the eventual bulk load is index-backed from day one.
"""

from __future__ import annotations

from typing import Iterable

from neo4j import Driver


def write_follows(driver: Driver, rows: Iterable[dict]) -> int:
    raise NotImplementedError(
        "Connections ingestion blocked on upstream schema — see ADR 0002 "
        "for the open questions."
    )


def write_friendships(driver: Driver, rows: Iterable[dict]) -> int:
    raise NotImplementedError(
        "Connections ingestion blocked on upstream schema — see ADR 0002 "
        "for the open questions."
    )
