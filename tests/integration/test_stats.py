"""GET /stats graph-diagnostics endpoint: seeded and empty-graph cases.

Mirrors the tool integration tests: reuses the `migrated_driver` and
`in_memory_audit` fixtures; spins up a minimal FastAPI app with the stats
router wired to the testcontainer driver and a real audit logger so both
the query and the audit write exercise live code paths.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from neo4j import Driver

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app(driver: Driver, audit: Any) -> FastAPI:
    """Minimal FastAPI app with only the stats router mounted."""
    from chorus.api.routers import stats as stats_router

    app = FastAPI()
    app.include_router(stats_router.router)
    app.state.driver = driver
    app.state.audit = audit
    return app


def _audit_rows(audit: Any) -> list[dict[str, Any]]:
    """Read all audit rows from the logger's SQLite, oldest first."""
    conn = sqlite3.connect(audit.db_path)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id")]
    finally:
        conn.close()


_AUTH = {"X-Auth-User": "analyst"}

# ---------------------------------------------------------------------------
# Seeded-graph tests
# ---------------------------------------------------------------------------


def _seed(driver: Driver) -> None:
    """Seed a small but representative graph for the stats assertions.

    Graph shape:
    - 2 Authors (auth-1, auth-2)
    - 3 Posts on 2 platforms:
        post-1 (Posting) on 'LinkedIn'  — authored by auth-1
        post-2 (Posting) on 'LinkedIn'  — authored by auth-2
        post-3 (Comment) on 'Twitter'   — authored by auth-1
    - 2 Aliases:
        alias-berlin — MENTIONS'd by post-1, post-2; RESOLVED_TO entity-1
        alias-paris  — MENTIONS'd by post-3; unresolved
    - 1 Entity (entity-1 = 'Berlin')
    - ingested_at set so latest_ingested_at is verifiable
    """
    with driver.session() as s:
        s.run(
            """
            MERGE (a1:Author {id: 'auth-1'})
              ON CREATE SET a1.handle = 'alice', a1.display_name = 'Alice'
            MERGE (a2:Author {id: 'auth-2'})
              ON CREATE SET a2.handle = 'bob', a2.display_name = 'Bob'

            MERGE (pl1:Platform {name: 'LinkedIn'})
            MERGE (pl2:Platform {name: 'Twitter'})

            MERGE (p1:Post:Posting {uuid: 'post-1'})
              ON CREATE SET p1.text = 'berlin post 1',
                            p1.ingested_at = datetime('2026-06-10T10:00:00+00:00')
            MERGE (p2:Post:Posting {uuid: 'post-2'})
              ON CREATE SET p2.text = 'berlin post 2',
                            p2.ingested_at = datetime('2026-06-11T10:00:00+00:00')
            MERGE (p3:Post:Comment {uuid: 'post-3'})
              ON CREATE SET p3.text = 'paris comment',
                            p3.ingested_at = datetime('2026-06-12T10:00:00+00:00')

            MERGE (a1)-[:AUTHORED]->(p1)
            MERGE (a2)-[:AUTHORED]->(p2)
            MERGE (a1)-[:AUTHORED]->(p3)

            MERGE (p1)-[:ON_PLATFORM]->(pl1)
            MERGE (p2)-[:ON_PLATFORM]->(pl1)
            MERGE (p3)-[:ON_PLATFORM]->(pl2)

            MERGE (al1:Alias {surface_form: 'Berlin'})
            MERGE (al2:Alias {surface_form: 'Paris'})

            MERGE (e1:Entity {id: 'entity-1'})
              ON CREATE SET e1.canonical_name = 'Berlin'
            MERGE (al1)-[:RESOLVED_TO]->(e1)

            MERGE (p1)-[:MENTIONS]->(al1)
            MERGE (p2)-[:MENTIONS]->(al1)
            MERGE (p3)-[:MENTIONS]->(al2)
            """
        )


def test_stats_seeded_graph(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """Seeded graph produces the expected counts, highlights, and platform split.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        in_memory_audit: Fresh audit logger over a temp SQLite file.
    """
    _seed(migrated_driver)
    client = TestClient(_build_app(migrated_driver, in_memory_audit))
    resp = client.get("/stats", headers=_AUTH)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # ----- node counts -----
    counts = body["counts"]
    assert counts["posts"] == 3
    assert counts["authors"] == 2
    assert counts["entities"] == 1
    assert counts["aliases"] == 2
    assert counts["platforms"] == 2

    # ----- edge counts -----
    edges = body["edges"]
    assert edges["mentions"] >= 3       # 3 MENTIONS edges total
    assert edges["authored"] >= 3       # 3 AUTHORED edges
    assert edges["resolved"] >= 1       # 1 RESOLVED_TO edge

    # ----- top_authors: auth-1 has 2 posts, auth-2 has 1 -----
    top_authors = body["top_authors"]
    assert len(top_authors) >= 1
    busiest = top_authors[0]
    assert busiest["count"] == 2        # auth-1 authored 2 posts
    assert busiest["author_id"] == "auth-1"

    # ----- posts_by_platform sums to 3 -----
    by_platform = body["posts_by_platform"]
    assert sum(row["count"] for row in by_platform) == 3

    # ----- resolution -----
    resolution = body["resolution"]
    assert resolution["total_aliases"] == 2
    assert resolution["resolved_aliases"] == 1

    # ----- latest_ingested_at is a non-empty ISO string -----
    lat = body["latest_ingested_at"]
    assert lat is not None
    assert len(lat) > 0

    # ----- top_entities: Berlin has 2 mention-posts, Paris has 1 -----
    top_entities = body["top_entities"]
    assert len(top_entities) >= 1
    assert top_entities[0]["name"] == "Berlin"
    assert top_entities[0]["count"] == 2


def test_stats_audit_row_written(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """GET /stats writes exactly one audit row with tool_name 'stats'.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        in_memory_audit: Fresh audit logger over a temp SQLite file.
    """
    client = TestClient(_build_app(migrated_driver, in_memory_audit))
    client.get("/stats", headers=_AUTH)

    rows = _audit_rows(in_memory_audit)
    assert len(rows) == 1
    row = rows[0]
    assert row["user"] == "analyst"
    assert row["tool_name"] == "stats"
    assert row["status"] == "ok"


# ---------------------------------------------------------------------------
# Empty-graph tests
# ---------------------------------------------------------------------------


def test_stats_empty_graph(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """Empty database returns all zeros, empty lists, null ingested_at — 200 OK.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        in_memory_audit: Fresh audit logger over a temp SQLite file.
    """
    client = TestClient(_build_app(migrated_driver, in_memory_audit))
    resp = client.get("/stats", headers=_AUTH)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    counts = body["counts"]
    for field in ("posts", "authors", "entities", "hashtags", "groups", "platforms", "aliases"):
        assert counts[field] == 0, f"expected counts.{field}==0, got {counts[field]}"

    edges = body["edges"]
    for field in ("mentions", "authored", "follows", "friends", "resolved"):
        assert edges[field] == 0, f"expected edges.{field}==0, got {edges[field]}"

    assert body["top_entities"] == []
    assert body["top_authors"] == []
    assert body["posts_by_platform"] == []
    assert body["latest_ingested_at"] is None

    resolution = body["resolution"]
    assert resolution["total_aliases"] == 0
    assert resolution["resolved_aliases"] == 0


# ---------------------------------------------------------------------------
# Null-guard regression: posts exist but NO MENTIONS / NO AUTHORED edges
# ---------------------------------------------------------------------------


def _seed_posts_no_mentions(driver: Driver) -> None:
    """Seed Post + Author + Platform nodes with AUTHORED and ON_PLATFORM edges but NO :MENTIONS edges.

    This exercises the null-guard added to the top_entities CALL{} subquery:
    without it, the OPTIONAL MATCH yields a null alias row that previously
    escaped as {name: null, count: 0} through collect().

    Author nodes DO have AUTHORED edges here so top_authors is non-empty —
    the top_entities null guard is the primary target.
    """
    with driver.session() as s:
        s.run(
            """
            MERGE (a1:Author {id: 'nm-author-1'})
              ON CREATE SET a1.handle = 'nomention', a1.display_name = 'No Mention User'
            MERGE (pl:Platform {name: 'TestNet'})
            MERGE (p1:Post:Posting {uuid: 'nm-post-1'})
              ON CREATE SET p1.text = 'no mentions here',
                            p1.ingested_at = datetime('2026-06-19T09:00:00+00:00')
            MERGE (p2:Post:Posting {uuid: 'nm-post-2'})
              ON CREATE SET p2.text = 'also no mentions',
                            p2.ingested_at = datetime('2026-06-19T10:00:00+00:00')
            MERGE (a1)-[:AUTHORED]->(p1)
            MERGE (a1)-[:AUTHORED]->(p2)
            MERGE (p1)-[:ON_PLATFORM]->(pl)
            MERGE (p2)-[:ON_PLATFORM]->(pl)
            """
        )


def test_stats_posts_with_no_mentions(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """Posts exist with AUTHORED edges but no MENTIONS edges.

    Regression: the top_entities CALL{} subquery previously produced a
    spurious {name: null, count: 0} entry when posts existed but carried
    no :MENTIONS edges.  After the Cypher null guard (WHERE name IS NOT NULL),
    top_entities must be [] while counts.posts > 0.

    top_authors should be non-empty because AUTHORED edges exist.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        in_memory_audit: Fresh audit logger over a temp SQLite file.
    """
    _seed_posts_no_mentions(migrated_driver)
    client = TestClient(_build_app(migrated_driver, in_memory_audit))
    resp = client.get("/stats", headers=_AUTH)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Posts exist — the row count is non-zero so we know the graph is not empty.
    assert body["counts"]["posts"] == 2, "expected 2 seeded posts"

    # top_entities must be empty: no :MENTIONS edges → no non-null alias rows.
    assert body["top_entities"] == [], (
        f"expected top_entities==[], got {body['top_entities']}"
    )

    # top_authors must be non-empty: AUTHORED edges exist.
    assert len(body["top_authors"]) >= 1
    assert body["top_authors"][0]["author_id"] == "nm-author-1"
    assert body["top_authors"][0]["count"] == 2
