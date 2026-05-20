"""Migrations apply idempotently and create the expected schema."""

from __future__ import annotations

from neo4j import Driver


def test_apply_is_idempotent(migrated_driver: Driver) -> None:
    from chorus.migrations.runner import apply_all

    second = apply_all(migrated_driver)
    assert second == []


def test_constraints_present(migrated_driver: Driver) -> None:
    expected = {
        "post_uuid",
        "author_id",
        "entity_id",
        "hashtag_tag",
        "platform_name",
        "group_id",
        "alias_surface",
        "migration_version",
    }
    with migrated_driver.session() as s:
        names = {r["name"] for r in s.run("SHOW CONSTRAINTS YIELD name")}
    missing = expected - names
    assert not missing, f"missing constraints: {missing}"


def test_vector_indexes_present(migrated_driver: Driver) -> None:
    with migrated_driver.session() as s:
        rows = s.run(
            "SHOW INDEXES YIELD name, type WHERE type = 'VECTOR' RETURN name"
        ).data()
    names = {r["name"] for r in rows}
    assert {"post_embedding", "entity_embedding"} <= names


def test_relationship_indexes_present(migrated_driver: Driver) -> None:
    """Connections-ingestion-ready: FOLLOWS / FRIENDS_WITH edges have indexes
    even though the ingestion path is stubbed."""
    with migrated_driver.session() as s:
        rows = s.run("SHOW INDEXES YIELD name, type RETURN name, type").data()
    names = {r["name"] for r in rows}
    assert {"follows_crawled", "friends_crawled", "mentions_model_version"} <= names
