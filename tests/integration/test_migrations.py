"""Migrations apply idempotently and create the expected schema."""

from __future__ import annotations

from neo4j import Driver


def test_apply_is_idempotent(migrated_driver: Driver) -> None:
    """Re-applying migrations on an up-to-date database is a no-op.

    Args:
        migrated_driver: Driver against an already-migrated database.
    """
    from chorus.migrations.runner import apply_all

    second = apply_all(migrated_driver)
    assert second == []


def test_constraints_present(migrated_driver: Driver) -> None:
    """Every uniqueness constraint the data model relies on exists.

    Acts as a regression guard against accidental migration ordering or
    constraint-name drift.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
    """
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
    """Post and Entity vector indexes are created and sized by EMBED_DIM.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
    """
    with migrated_driver.session() as s:
        rows = s.run("SHOW INDEXES YIELD name, type WHERE type = 'VECTOR' RETURN name").data()
    names = {r["name"] for r in rows}
    assert {"post_embedding", "entity_embedding"} <= names


def test_relationship_indexes_present(migrated_driver: Driver) -> None:
    """Edge indexes for FOLLOWS / FRIENDS_WITH / MENTIONS are present.

    Connections ingestion is stubbed but the indexes exist already so
    the eventual bulk load is index-backed from day one.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
    """
    with migrated_driver.session() as s:
        rows = s.run("SHOW INDEXES YIELD name, type RETURN name, type").data()
    names = {r["name"] for r in rows}
    assert {"follows_crawled", "friends_crawled", "mentions_model_version"} <= names
