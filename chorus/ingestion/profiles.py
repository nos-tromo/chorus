"""Profile (author-profile enrichment) DTO + graph write.

The upstream system emits two tables under the "connections" umbrella: a
node-edge-node social-graph table (handled by `connections.py`) and this
profile-per-row table. This module handles the latter — one row per
author profile — and writes it as enrichment onto the `:Author` nodes
the artifact stages already create. It does not build social-graph
edges: the relationship columns (`Friends`, `Connected Users`, ...) are
denormalized duplicates of edges the artifact and edge tables own and
are preserved in the raw store only. See ADR 0006.

The join key is the upstream `ID` column — the network author id, equal
to the `Author ID` carried by the postings/comments tables and used as
`:Author.id`. The upstream `UUID` is kept as `profile_uuid` for
traceability but is never a graph key.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from neo4j import Driver
from pydantic import BaseModel, Field


class ProfileDTO(BaseModel):
    """Validated DTO for one upstream ``profiles`` row.

    Profiles are enrichment for ``:Author`` nodes, not standalone
    entities. Unlike the artifact DTOs there is no ``retention_until``
    field — personal data on ``:Author`` is retained indefinitely (see
    ADR 0006 and ``docs/compliance.md``).

    Attributes:
        id: Network author id (the join key, equal to ``:Author.id``).
        profile_uuid: Upstream UUID for the profile row, kept for
            traceability only.
        url: Profile URL.
        network_object_id: Upstream-side network object id.
        crawled_at: Ingestion-side timestamp.
        last_updated: Profile edit timestamp.
        display_name: Human-readable name.
        vanity_name: Platform-specific slug.
        profile_type: Upstream profile-type label.
        platform: Platform name (also stored on the related ``:Author``).
        system_tags: Upstream ``Tags`` field as a string list.
        bio: Free-text biography.
        date_of_birth: Date of birth as supplied (string for upstream-
            format compatibility — not parsed).
        hometown: Hometown string as supplied.
        work_education: Work/education string as supplied.
        current_city: Current city string as supplied.
        additional_details: Catch-all upstream field.
    """

    id: str
    profile_uuid: str
    url: str | None = None
    network_object_id: str | None = None
    crawled_at: datetime | None = None
    last_updated: datetime | None = None
    display_name: str | None = None
    vanity_name: str | None = None
    profile_type: str | None = None
    platform: str | None = None
    system_tags: list[str] = Field(default_factory=list)
    bio: str | None = None
    date_of_birth: str | None = None
    hometown: str | None = None
    work_education: str | None = None
    current_city: str | None = None
    additional_details: str | None = None


def from_row(row: dict[str, Any]) -> ProfileDTO:
    """Adapt one upstream ``profiles`` row to a :class:`ProfileDTO`.

    Field-name mapping mirrors the upstream table headers documented in
    CLAUDE.md §Upstream data format. Unlike the artifact tables,
    profiles carry no retention timer.

    Args:
        row: One raw row as returned by the upstream adapter.

    Returns:
        A populated, validated :class:`ProfileDTO`.

    Raises:
        KeyError: If a required upstream column (``ID``, ``UUID``) is
            missing.
        pydantic.ValidationError: If the resulting DTO fails validation.
    """
    return ProfileDTO(
        id=str(row["ID"]),
        profile_uuid=str(row["UUID"]),
        url=_str_or_none(row.get("URL")),
        network_object_id=_str_or_none(row.get("Network Object ID")),
        crawled_at=_coerce_dt_opt(row.get("Crawled at")),
        last_updated=_coerce_dt_opt(row.get("Date Last Updated")),
        display_name=_str_or_none(row.get("Name")),
        vanity_name=_str_or_none(row.get("Vanity Name")),
        profile_type=_str_or_none(row.get("Profile Type")),
        platform=_str_or_none(row.get("Network")),
        system_tags=_tags(row.get("Tags")),
        bio=_str_or_none(row.get("Bio")),
        date_of_birth=_str_or_none(row.get("Date of Birth")),
        hometown=_str_or_none(row.get("Hometown")),
        work_education=_str_or_none(row.get("Work/Education")),
        current_city=_str_or_none(row.get("Current City")),
        additional_details=_str_or_none(row.get("Additional Details")),
    )


def write(driver: Driver, dto: ProfileDTO) -> None:
    """MERGE the :class:`Author` by ``id`` and enrich it from this DTO.

    The profiles table is the authoritative source for author identity,
    so this uses ``SET`` (overwrite) rather than the write-once
    ``ON CREATE SET`` the artifact stages use. ``$props`` carries only
    the columns the row actually supplied, so a sparse upstream row
    never wipes a property an earlier stage or crawl already set.

    Args:
        driver: Open Neo4j driver.
        dto: Validated profile DTO whose fields enrich the matching
            ``:Author`` node.
    """
    cypher = """
    MERGE (a:Author {id: $id})
    SET a += $props
    SET a.crawled_at   = CASE WHEN $crawled_at IS NULL THEN a.crawled_at
                              ELSE datetime($crawled_at) END,
        a.last_updated = CASE WHEN $last_updated IS NULL THEN a.last_updated
                              ELSE datetime($last_updated) END
    """
    props = dto.model_dump(
        mode="json",
        exclude_none=True,
        exclude={"id", "crawled_at", "last_updated"},
    )
    # exclude_none keeps an empty system_tags list; drop it so a sparse
    # crawl doesn't overwrite tags an earlier crawl already set.
    if not props.get("system_tags"):
        props.pop("system_tags", None)
    with driver.session() as s:
        s.run(
            cypher,
            id=dto.id,
            props=props,
            crawled_at=dto.crawled_at.isoformat() if dto.crawled_at else None,
            last_updated=dto.last_updated.isoformat() if dto.last_updated else None,
        )


def _coerce_dt_opt(value: Any) -> datetime | None:
    """Coerce ``value`` to an aware UTC datetime, or ``None`` when missing.

    Naive datetimes are assumed to be UTC; strings are parsed via
    :meth:`datetime.fromisoformat`.

    Args:
        value: Candidate value (``None``, empty string, datetime, or
            ISO-8601 string).

    Returns:
        ``None`` for missing/empty inputs; a timezone-aware datetime
        otherwise.

    Raises:
        ValueError: If ``value`` is a string that does not parse as ISO-8601.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).strip())


def _str_or_none(value: Any) -> str | None:
    """Coerce ``value`` to a string, or ``None`` when missing.

    Args:
        value: Candidate value.

    Returns:
        ``None`` for ``None``/empty inputs; ``str(value)`` otherwise.
    """
    if value is None or value == "":
        return None
    return str(value)


def _tags(value: Any) -> list[str]:
    """Parse the upstream ``Tags`` column into a list of tag strings.

    Args:
        value: Either a list of stringifiable items, a comma-separated
            string, or ``None``/empty for "no tags".

    Returns:
        Tag strings in source order, with empties removed.
    """
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [t.strip() for t in str(value).split(",") if t.strip()]
