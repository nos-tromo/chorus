"""profiles ingestion: row parsing and :Author enrichment."""

from __future__ import annotations

from datetime import datetime, timezone

from neo4j import Driver


_SAMPLE_ROW = {
    "UUID": "prof-1",
    "ID": "a-1",
    "URL": "https://example.test/alice",
    "Network Object ID": "nob-1",
    "Crawled at": "2026-05-02T10:00:00+00:00",
    "Date Last Updated": "2026-05-01T09:00:00+00:00",
    "Name": "Alice Anderson",
    "Vanity Name": "alice",
    "Profile Type": "person",
    "Network": "linkedin",
    "Tags": "verified, staff",
    "Bio": "Berlin-based analyst",
    "Date of Birth": "1990-03-15",
    "Hometown": "Hamburg",
    "Work/Education": "Acme Corp",
    "Current City": "Berlin",
    "Additional Details": "misc",
    # denormalized columns — present in the row, not modeled in the graph
    "Friends": "a-2,a-3",
    "Connected Users": "a-4",
    "Target Profile": "",
    "Profile Owner": "",
}


def test_from_row_maps_all_fields() -> None:
    """Every upstream column maps to the expected DTO field.

    Acts as the executable mapping spec for ``profiles.from_row``;
    failures here usually mean either a column rename in the upstream
    or a regression in the parser.
    """
    from chorus.ingestion.profiles import from_row

    dto = from_row(_SAMPLE_ROW)
    assert dto.id == "a-1"
    assert dto.profile_uuid == "prof-1"
    assert dto.url == "https://example.test/alice"
    assert dto.network_object_id == "nob-1"
    assert dto.display_name == "Alice Anderson"
    assert dto.vanity_name == "alice"
    assert dto.profile_type == "person"
    assert dto.platform == "linkedin"
    assert dto.system_tags == ["verified", "staff"]
    assert dto.bio == "Berlin-based analyst"
    assert dto.date_of_birth == "1990-03-15"
    assert dto.hometown == "Hamburg"
    assert dto.work_education == "Acme Corp"
    assert dto.current_city == "Berlin"
    assert dto.additional_details == "misc"
    assert dto.crawled_at == datetime(2026, 5, 2, 10, 0, tzinfo=timezone.utc)
    assert dto.last_updated == datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)


def test_from_row_handles_missing_and_empty() -> None:
    """Missing columns and empty strings normalize to ``None`` / empty lists.

    Guards against a sparse upstream row inadvertently producing
    truthy-but-empty values on the DTO.
    """
    from chorus.ingestion.profiles import from_row

    dto = from_row({"ID": "a-9", "UUID": "prof-9", "Bio": "", "Tags": ""})
    assert dto.id == "a-9"
    assert dto.profile_uuid == "prof-9"
    assert dto.bio is None
    assert dto.system_tags == []
    assert dto.crawled_at is None
    assert dto.display_name is None


def test_write_creates_author(migrated_driver: Driver) -> None:
    """``write`` MERGEs an :Author and sets every enrichment property.

    Includes the temporal coercion check — ``crawled_at`` must land on
    the node as a Neo4j temporal, not as the source string.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
    """
    from chorus.ingestion.profiles import from_row, write

    write(migrated_driver, from_row(_SAMPLE_ROW))

    with migrated_driver.session() as s:
        a = s.run("MATCH (a:Author {id: 'a-1'}) RETURN a").single()["a"]
        assert a["profile_uuid"] == "prof-1"
        assert a["display_name"] == "Alice Anderson"
        assert a["bio"] == "Berlin-based analyst"
        assert a["date_of_birth"] == "1990-03-15"
        assert a["system_tags"] == ["verified", "staff"]
        # crawled_at coerced to a temporal type, not stored as a string
        assert a["crawled_at"].year == 2026
        count = s.run("MATCH (a:Author) RETURN count(a) AS c").single()["c"]
        assert count == 1


def test_write_enriches_existing_thin_author(migrated_driver: Driver) -> None:
    """``write`` enriches a thin :Author left by the postings stage.

    The postings stage uses ``ON CREATE SET`` (write-once); the profiles
    stage uses ``SET`` (overwrite) and is the authoritative source for
    identity. This test pins both behaviors: ``display_name`` is
    overwritten by the profile, but the posting-set ``handle`` is left
    intact.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
    """
    from chorus.ingestion.profiles import from_row, write

    # a thin :Author as the postings stage would leave it
    with migrated_driver.session() as s:
        s.run(
            "MERGE (a:Author {id: 'a-1'}) "
            "ON CREATE SET a.handle = 'alice', a.display_name = 'Alice', "
            "a.platform = 'linkedin'"
        )

    write(migrated_driver, from_row(_SAMPLE_ROW))

    with migrated_driver.session() as s:
        a = s.run("MATCH (a:Author {id: 'a-1'}) RETURN a").single()["a"]
        # profiles is authoritative — display_name overwritten
        assert a["display_name"] == "Alice Anderson"
        # the postings-set handle is left intact
        assert a["handle"] == "alice"
        # personal fields added
        assert a["bio"] == "Berlin-based analyst"
        count = s.run("MATCH (a:Author) RETURN count(a) AS c").single()["c"]
        assert count == 1


def test_write_is_idempotent(migrated_driver: Driver) -> None:
    """Writing the same profile twice produces exactly one :Author node.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
    """
    from chorus.ingestion.profiles import from_row, write

    dto = from_row(_SAMPLE_ROW)
    write(migrated_driver, dto)
    write(migrated_driver, dto)

    with migrated_driver.session() as s:
        count = s.run("MATCH (a:Author) RETURN count(a) AS c").single()["c"]
        assert count == 1


def test_write_sparse_row_does_not_wipe(migrated_driver: Driver) -> None:
    """A sparse later crawl never clobbers properties an earlier crawl set.

    Confirms the ``exclude_none`` strategy plus the empty-tags drop
    keep previously-set personal fields intact when a subsequent crawl
    omits them.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
    """
    from chorus.ingestion.profiles import from_row, write

    write(migrated_driver, from_row(_SAMPLE_ROW))
    # a later, sparser crawl of the same profile omits Bio, Tags, and dates
    write(migrated_driver, from_row({"ID": "a-1", "UUID": "prof-1"}))

    with migrated_driver.session() as s:
        a = s.run("MATCH (a:Author {id: 'a-1'}) RETURN a").single()["a"]
        assert a["bio"] == "Berlin-based analyst"
        assert a["system_tags"] == ["verified", "staff"]
        assert a["crawled_at"].year == 2026
