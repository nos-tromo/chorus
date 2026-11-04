"""Synthetic graphs for the retention tests.

Posts are written with the real ingestion writers, so the graph has the
shape production ingestion gives it; a post's deadline is set through its
ingestion time. ``NOW`` lies in the future so that raw-store rows, which
carry the wall-clock time they were written, are older than the retention
period. Every name, id and text is invented.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from neo4j import Driver

from chorus.audit.trail import AuditContext
from chorus.ingestion.raw_store import RawStore
from chorus.retention.sweeper import SweepTotals, sweep
from chorus.utils.env_cfg import RetentionConfig, RetentionSweepConfig

PERIOD = 30
NOW = datetime.now(UTC).replace(microsecond=0) + timedelta(days=100)
#: Ingested long enough ago that the deadline (ingestion + PERIOD) has passed.
OLD = NOW - timedelta(days=PERIOD + 10)
#: Ingested recently enough that the deadline is still ahead.
RECENT = NOW - timedelta(days=PERIOD - 5)
RETENTION = RetentionConfig(default_days=PERIOD)
#: Retention off at ingestion: the post gets no deadline.
NO_DEADLINE = RetentionConfig(default_days=PERIOD, enabled=False)


def sweep_config(batch_size: int = 500, *, enabled: bool = True) -> RetentionSweepConfig:
    """Return a sweeper configuration for a test.

    Args:
        batch_size: Posts per batch.
        enabled: The switch.

    Returns:
        The configuration, with no schedule.
    """
    return RetentionSweepConfig(enabled=enabled, interval_s=0, batch_size=batch_size)


def raw_store() -> RawStore:
    """Return the test's raw store (under its ``CHORUS_HOME``), schema applied.

    Returns:
        The raw store.
    """
    from chorus.utils.env_cfg import load_path_env

    raw = RawStore(load_path_env().raw_store)
    raw.init_schema()
    return raw


def run_sweep(
    driver: Driver,
    home: Path,
    audit: AuditContext,
    *,
    batch_size: int = 500,
    retention: RetentionConfig = RETENTION,
    now: datetime = NOW,
) -> SweepTotals:
    """Run one sweep at ``NOW`` against the test's stores.

    Args:
        driver: Driver against the migrated test database.
        home: The test's ``CHORUS_HOME``.
        audit: The audit context to record under.
        batch_size: Posts per batch.
        retention: The retention period in force.
        now: The cutoff.

    Returns:
        What the sweep deleted.
    """
    return sweep(
        driver,
        raw_store(),
        home,
        audit=audit,
        retention=retention,
        config=sweep_config(batch_size),
        now=now,
    )


def posting(
    driver: Driver,
    uuid: str,
    *,
    author: str,
    at: datetime = OLD,
    retention: RetentionConfig = RETENTION,
    **extra: Any,
) -> None:
    """Write a posting, and its raw row.

    Args:
        driver: Open driver.
        uuid: The posting's uuid.
        author: Its author's id.
        at: Its ingestion time.
        retention: The retention setting it is ingested under.
        **extra: Further upstream columns (``Co-Author``, ``Filename``, ...).
    """
    from chorus.ingestion import postings

    row = {"UUID": uuid, "Author ID": author, "Network": "examplenet", "Text Content": f"text of {uuid}", **extra}
    raw_store().write_batch("postings", [row])
    postings.write(driver, postings.from_row(row, retention, at))


def comment(
    driver: Driver,
    uuid: str,
    *,
    on: str,
    author: str,
    replies_to: str | None = None,
    at: datetime = OLD,
    retention: RetentionConfig = RETENTION,
) -> None:
    """Write a comment on a posting, and its raw row.

    Args:
        driver: Open driver.
        uuid: The comment's uuid.
        on: The posting's uuid.
        author: Its author's id.
        replies_to: The uuid of the comment it replies to, if any.
        at: Its ingestion time.
        retention: The retention setting it is ingested under.
    """
    from chorus.ingestion import comments

    row: dict[str, Any] = {
        "UUID": uuid,
        "Author ID": author,
        "Network": "examplenet",
        "Text Content": f"text of {uuid}",
        "Posting Text": f"text of {on}",
    }
    raw_store().write_batch("comments", [row])
    augmented = {**row, "Parent Posting UUID": on, "Parent Comment UUID": replies_to}
    comments.write(driver, comments.from_row(augmented, retention, at))


def message(
    driver: Driver,
    uuid: str,
    *,
    sender: str,
    chat: str = "chat-1",
    replies_to: str | None = None,
    at: datetime = OLD,
    retention: RetentionConfig = RETENTION,
) -> None:
    """Write a chat message, and its raw row.

    Args:
        driver: Open driver.
        uuid: The message's uuid.
        sender: Its sender's author id.
        chat: The chat group's id.
        replies_to: The uuid of the message it replies to, if any.
        at: Its ingestion time.
        retention: The retention setting it is ingested under.
    """
    from chorus.ingestion import messages

    row: dict[str, Any] = {
        "UUID": uuid,
        "Chat ID": chat,
        "Sender": sender,
        "Network": "examplenet",
        "Text": f"text of {uuid}",
    }
    if replies_to:
        row["Reply To"] = replies_to
    raw_store().write_batch("messages", [row])
    messages.write(driver, messages.from_row(row, retention, at))


def mention(driver: Driver, post: str, surface: str, *, entity: str | None = None) -> None:
    """Make a post mention an alias, optionally resolved to an entity.

    Args:
        driver: Open driver.
        post: The post's uuid.
        surface: The alias's surface form.
        entity: An entity id to resolve the alias to (created if missing).
    """
    from chorus.ingestion.extraction import write_mentions

    span = {
        "surface_form": surface,
        "label": "person",
        "span_start": 0,
        "span_end": len(surface),
        "confidence": 0.9,
        "post_uuid": post,
        "model_version": "test-ner",
    }
    write_mentions(driver, post, [span])
    if entity is not None:
        with driver.session() as s:
            s.run(
                "MATCH (al:Alias {surface_form: $surface}) "
                "MERGE (e:Entity {id: $entity}) ON CREATE SET e.canonical_name = $surface "
                "MERGE (al)-[:RESOLVED_TO]->(e)",
                surface=surface,
                entity=entity,
            ).consume()


def hashtag(driver: Driver, post: str, tag: str) -> None:
    """Give a post a hashtag.

    Args:
        driver: Open driver.
        post: The post's uuid.
        tag: The hashtag.
    """
    with driver.session() as s:
        s.run(
            "MATCH (p:Post {uuid: $post}) MERGE (h:Hashtag {tag: $tag}) MERGE (p)-[:HAS_HASHTAG]->(h)",
            post=post,
            tag=tag,
        ).consume()


def follows(driver: Driver, follower: str, followed: str) -> None:
    """Add a ``:FOLLOWS`` edge between two authors.

    Args:
        driver: Open driver.
        follower: The following author's id.
        followed: The followed author's id.
    """
    with driver.session() as s:
        s.run(
            "MERGE (a:Author {id: $a}) MERGE (b:Author {id: $b}) MERGE (a)-[:FOLLOWS]->(b)", a=follower, b=followed
        ).consume()


def profile(driver: Driver, author: str) -> None:
    """Enrich an author with profile data, and store the profile's raw row.

    Args:
        driver: Open driver.
        author: The author's id.
    """
    from chorus.ingestion import profiles

    row = {
        "ID": author,
        "UUID": f"profile-{author}",
        "Name": f"Name of {author}",
        "Network": "examplenet",
        "Bio": "an invented biography",
        "Date of Birth": "1990-01-01",
    }
    raw_store().write_batch("profiles", [row])
    profiles.write(driver, profiles.from_row(row))


def uuids(driver: Driver, label: str = "Post") -> set[str]:
    """Return the uuids of the posts in the graph.

    Args:
        driver: Open driver.
        label: Narrow to ``Posting``, ``Comment`` or ``Message``.

    Returns:
        The uuids.
    """
    with driver.session() as s:
        return {str(r["u"]) for r in s.run(f"MATCH (p:{label}) RETURN p.uuid AS u")}


def count(driver: Driver, label: str) -> int:
    """Count the nodes with a label.

    Args:
        driver: Open driver.
        label: The label.

    Returns:
        The count.
    """
    with driver.session() as s:
        record = s.run(f"MATCH (n:{label}) RETURN count(n) AS n").single()
    return int(record["n"]) if record else 0


def raw_uuids(kind: str | None = None) -> list[str]:
    """Return the uuids of the raw store's rows, one per row.

    Args:
        kind: Only rows of this table.

    Returns:
        The uuids, sorted, with repeats for repeated rows.
    """
    with raw_store().reading() as conn:
        if kind is None:
            rows = conn.execute("SELECT uuid FROM raw_rows").fetchall()
        else:
            rows = conn.execute("SELECT uuid FROM raw_rows WHERE kind = ?", (kind,)).fetchall()
    return sorted(str(uuid) for (uuid,) in rows)
