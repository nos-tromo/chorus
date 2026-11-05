"""The retention dry run: what a sweep would delete now, as counts and date ranges.

The report applies the same rules as :mod:`chorus.retention.sweeper` at one
cutoff, without deleting: which posts would go (expired, and the comments
and replies that go with them), what they alone reference, their raw-store
rows, raw rows no post holds, stale staged uploads, and what stays. It also
shows what a sweep leaves behind that is kept by policy: authors whose posts
all go (ADR 0006 keeps author data indefinitely), entities that lose their
last alias, and groups that lose their last post.

It reads only what it needs to count: counts, minima and maxima of
timestamps, the uuids it matches against the raw store. It returns no ids,
names or content, records nothing on the audit trail, writes nothing (the
raw store is opened read-only) and works with the sweeper switched off.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from itertools import chain
from pathlib import Path
from typing import Any, Final

from neo4j import Driver, Session

from chorus.ingestion.raw_store import RawStore, zero_counts
from chorus.retention.sweeper import audit_time, describe_config, quiet_session, rows_without_post, stale_uploads
from chorus.utils.env_cfg import RetentionConfig, RetentionSweepConfig

#: Whether post ``q`` goes in a sweep at ``$cutoff``: it, or a post it
#: belongs to (its posting, the comment or message it replies to), expired.
_DOOMED: Final = "EXISTS { MATCH (q)-[:ON|REPLIES_TO*0..]->(r) WHERE r.retention_until <= $cutoff }"

#: Every post a sweep at ``$cutoff`` deletes, as ``p``.
_DELETED: Final = """
MATCH (root:Post) WHERE root.retention_until <= $cutoff
MATCH (p:Post)-[:ON|REPLIES_TO*0..]->(root)
WITH DISTINCT p
"""

_POSTS: Final = (
    _DELETED
    + """
RETURN count(p) AS total,
       count(CASE WHEN p:Posting THEN 1 END) AS postings,
       count(CASE WHEN p:Comment THEN 1 END) AS comments,
       count(CASE WHEN p:Message THEN 1 END) AS messages,
       count(CASE WHEN p.retention_until <= $cutoff THEN 1 END) AS expired,
       min(CASE WHEN p.retention_until <= $cutoff THEN p.retention_until END) AS deadline_earliest,
       max(CASE WHEN p.retention_until <= $cutoff THEN p.retention_until END) AS deadline_latest,
       min(p.ingested_at) AS ingested_oldest,
       max(p.ingested_at) AS ingested_newest
"""
)

_POST_UUIDS: Final = _DELETED + "RETURN p.uuid AS uuid"

# Bare parent references whose every neighbour is a deleted post.
_PARENT_REFS: Final = (
    _DELETED
    + """
MATCH (p)-[:ON|REPLIES_TO]->(q:Post)
WHERE q.ingested_at IS NULL AND q.text IS NULL AND NOT """
    + _DOOMED
    + """
WITH DISTINCT q AS s
WHERE NOT EXISTS {
  MATCH (s)--(q)
  WHERE NOT (q:Post AND """
    + _DOOMED
    + """)
}
RETURN s.uuid AS uuid,
       CASE WHEN s:Posting THEN 'postings' WHEN s:Comment THEN 'comments' ELSE 'messages' END AS kind
"""
)

# Aliases, hashtags and attachments only deleted posts reference.
_LINKED: Final = (
    _DELETED
    + """
MATCH (p)-[:MENTIONS|HAS_HASHTAG|HAS_ATTACHMENT]->(x)
WITH DISTINCT x
WHERE NOT EXISTS {
  MATCH (x)<-[:MENTIONS|HAS_HASHTAG|HAS_ATTACHMENT]-(q:Post)
  WHERE NOT """
    + _DOOMED
    + """
}
RETURN count(CASE WHEN x:Alias THEN 1 END) AS aliases,
       count(CASE WHEN x:Hashtag THEN 1 END) AS hashtags,
       count(CASE WHEN x:Attachment THEN 1 END) AS attachments
"""
)

# Entities whose every alias goes: no alias of theirs keeps a mention from a
# post that stays (an alias no post mentions is not touched by a sweep).
_ENTITIES: Final = (
    _DELETED
    + """
MATCH (p)-[:MENTIONS]->(:Alias)-[:RESOLVED_TO]->(e:Entity)
WITH DISTINCT e
WHERE NOT EXISTS {
  MATCH (e)<-[:RESOLVED_TO]-(al:Alias)
  WHERE NOT EXISTS { (al)<-[:MENTIONS]-(:Post) }
     OR EXISTS { MATCH (al)<-[:MENTIONS]-(q:Post) WHERE NOT """
    + _DOOMED
    + """ }
}
RETURN count(e) AS entities
"""
)

_GROUPS: Final = (
    _DELETED
    + """
MATCH (p)-[:IN_GROUP|IN_CHAT]->(g:Group)
WITH DISTINCT g
WHERE NOT EXISTS { MATCH (g)<-[:IN_GROUP|IN_CHAT]-(q:Post) WHERE NOT """
    + _DOOMED
    + """ }
RETURN count(g) AS groups
"""
)

# The authors, co-authors and quoted users of the deleted posts, and what
# each keeps: a post that stays, a social edge, profile data.
_AUTHORS: Final = (
    _DELETED
    + """
MATCH (a:Author)-[:AUTHORED|CO_AUTHORED|QUOTED_IN]->(p)
WITH DISTINCT a
WITH a,
     EXISTS { MATCH (a)-[:AUTHORED|CO_AUTHORED|QUOTED_IN]->(q:Post) WHERE NOT """
    + _DOOMED
    + """ } AS keeps_post,
     EXISTS { (a)-[:FOLLOWS|FRIENDS_WITH]-(:Author) } AS social
RETURN count(a) AS of_deleted_posts,
       count(CASE WHEN NOT keeps_post THEN 1 END) AS left_without_posts,
       count(CASE WHEN NOT keeps_post AND social THEN 1 END) AS left_with_social_edges_only,
       count(CASE WHEN NOT keeps_post AND NOT social THEN 1 END) AS left_without_posts_or_social_edges,
       count(CASE WHEN NOT keeps_post AND NOT social AND a.profile_uuid IS NOT NULL THEN 1 END)
         AS of_those_with_profile_data
"""
)

_AUTHORS_ALREADY_ISOLATED: Final = """
MATCH (a:Author) WHERE NOT EXISTS { (a)--() }
RETURN count(a) AS without_posts_or_social_edges,
       count(CASE WHEN a.profile_uuid IS NOT NULL THEN 1 END) AS of_those_with_profile_data
"""

_KEPT_POSTS: Final = (
    """
MATCH (q:Post)
WITH q, """
    + _DOOMED
    + """ AS doomed
WITH q WHERE NOT doomed
RETURN count(q) AS total,
       count(CASE WHEN q.retention_until > $cutoff THEN 1 END) AS not_due,
       min(CASE WHEN q.retention_until > $cutoff THEN q.retention_until END) AS next_deadline,
       count(CASE WHEN q.retention_until IS NULL AND q.ingested_at IS NOT NULL THEN 1 END) AS retention_off,
       count(CASE WHEN q.retention_until IS NULL AND q.ingested_at IS NULL AND q.text IS NOT NULL THEN 1 END)
         AS before_ingestion_times,
       count(CASE WHEN q.retention_until IS NULL AND q.ingested_at IS NULL AND q.text IS NULL THEN 1 END)
         AS parent_refs,
       count(CASE WHEN q.retention_until IS NULL AND q.ingested_at IS NULL AND q.text IS NULL
                   AND EXISTS { (q)<-[:AUTHORED]-(:Author) } THEN 1 END) AS unfilled
"""
)


def _when(value: Any) -> str | None:
    """Render a Neo4j or Python time as an audit-style timestamp.

    Args:
        value: A ``neo4j.time.DateTime``, a ``datetime``, or ``None``.

    Returns:
        ISO 8601 UTC, or ``None``.
    """
    if value is None:
        return None
    native = value.to_native() if hasattr(value, "to_native") else value
    return audit_time(native)


def _days_before(cutoff: datetime, value: Any) -> int | None:
    """Return how many whole days before the cutoff a time lies.

    Args:
        cutoff: The report's cutoff.
        value: A ``neo4j.time.DateTime``, a ``datetime``, or ``None``.

    Returns:
        The number of days, or ``None``.
    """
    if value is None:
        return None
    native = value.to_native() if hasattr(value, "to_native") else value
    return (cutoff - native).days


def _single(session: Session, query: str, cutoff: datetime) -> dict[str, Any]:
    """Run an aggregate query and return its one row.

    Args:
        session: An open session.
        query: A query returning exactly one row.
        cutoff: The ``$cutoff`` parameter.

    Returns:
        The row as a mapping.
    """
    record = session.run(query, cutoff=cutoff).single()
    return record.data() if record is not None else {}


def _uuid_chunks(session: Session, query: str, cutoff: datetime, size: int = 500) -> Iterator[list[str]]:
    """Stream the uuids a query returns in chunks, without holding them all.

    Args:
        session: An open session.
        query: A query returning ``uuid`` rows.
        cutoff: The ``$cutoff`` parameter.
        size: Uuids per chunk.

    Yields:
        Lists of at most ``size`` uuids.
    """
    chunk: list[str] = []
    for record in session.run(query, cutoff=cutoff):
        chunk.append(str(record["uuid"]))
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _raw_store_part(
    driver: Driver,
    raw_path: Path,
    cutoff: datetime,
    retention: RetentionConfig,
    batch_size: int,
    deleted_uuids: Iterable[list[str]],
) -> dict[str, Any]:
    """Count the raw rows a sweep deletes, and those it keeps by policy.

    Args:
        driver: Open Neo4j driver.
        raw_path: The raw store's file; it is not created when missing.
        cutoff: The report's cutoff.
        retention: The retention period in force.
        batch_size: Raw rows checked per page, as the sweep does.
        deleted_uuids: The deleted posts' uuids, in chunks, consumed once.

    Returns:
        ``of_deleted_posts`` and ``without_post`` per post table (the
        latter ``None`` while retention is off: the sweep then keeps those
        rows), the receive-time range of the latter, and the author rows
        kept.
    """
    of_deleted = zero_counts()
    without_post: dict[str, Any] = {**zero_counts(), "received_oldest": None, "received_newest": None}
    part: dict[str, Any] = {
        "of_deleted_posts": of_deleted,
        "without_post": without_post if retention.enabled else None,
        "kept_author_rows": {"profiles": 0, "connections": 0},
    }
    received_before = cutoff - timedelta(days=retention.default_days)
    if retention.enabled:
        part["without_post_received_before"] = audit_time(received_before)
    if not raw_path.is_file():
        return part
    raw = RawStore(raw_path)
    with raw.reading(read_only=True) as conn:
        if not RawStore.has_table(conn):
            return part
        for chunk in deleted_uuids:
            for kind, n in RawStore.count_post_rows(conn, chunk).items():
                of_deleted[kind] += n
        counts = RawStore.count_by_kind(conn)
        part["kept_author_rows"] = {kind: counts.get(kind, 0) for kind in ("profiles", "connections")}
        if not retention.enabled:
            return part
        after_id = 0
        while True:
            page = RawStore.post_rows_received_before(conn, received_before, after_id=after_id, limit=batch_size)
            if not page:
                break
            after_id = page[-1].id
            for row in rows_without_post(driver, page):
                without_post[row.kind] += 1
                oldest, newest = without_post["received_oldest"], without_post["received_newest"]
                without_post["received_oldest"] = row.fetched_at if oldest is None else min(oldest, row.fetched_at)
                without_post["received_newest"] = row.fetched_at if newest is None else max(newest, row.fetched_at)
    for key in ("received_oldest", "received_newest"):
        if without_post[key] is not None:
            without_post[key] = audit_time(datetime.fromisoformat(without_post[key]))
    return part


def _uploads_part(home: Path, cutoff: datetime, retention: RetentionConfig) -> dict[str, Any] | None:
    """Count the stale staged uploads a sweep deletes.

    Args:
        home: ``CHORUS_HOME``.
        cutoff: The report's cutoff.
        retention: The retention period in force.

    Returns:
        Directories, files and the oldest modification time; ``None`` while
        retention is off (the sweep then leaves uploads alone).
    """
    if not retention.enabled:
        return None
    received_before = cutoff - timedelta(days=retention.default_days)
    stale = stale_uploads(home / "uploads", received_before)
    times = [entry.lstat().st_mtime for staging in stale for entry in [staging, *staging.iterdir()]]
    return {
        "received_before": audit_time(received_before),
        "directories": len(stale),
        "files": sum(1 for staging in stale for _ in staging.iterdir()),
        "oldest": audit_time(datetime.fromtimestamp(min(times), UTC)) if times else None,
    }


def build_report(
    driver: Driver,
    raw_path: Path,
    home: Path,
    *,
    retention: RetentionConfig,
    config: RetentionSweepConfig,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Count what a sweep would delete now, and what it would leave.

    Args:
        driver: Open Neo4j driver.
        raw_path: The raw store's file.
        home: ``CHORUS_HOME``.
        retention: The retention period in force.
        config: The sweeper configuration (reported, and the raw-store page
            size).
        now: The cutoff; defaults to the current time.

    Returns:
        The report: plain counts, timestamps and day counts, no ids.
    """
    cutoff = (now or datetime.now(UTC)).astimezone(UTC)
    with quiet_session(driver) as session:
        posts = _single(session, _POSTS, cutoff)
        parent_refs = [(str(r["uuid"]), str(r["kind"])) for r in session.run(_PARENT_REFS, cutoff=cutoff)]
        parent_ref_uuids = [uuid for uuid, _ in parent_refs]
        linked = _single(session, _LINKED, cutoff)
        entities = _single(session, _ENTITIES, cutoff)
        groups = _single(session, _GROUPS, cutoff)
        authors = _single(session, _AUTHORS, cutoff)
        already = _single(session, _AUTHORS_ALREADY_ISOLATED, cutoff)
        kept = _single(session, _KEPT_POSTS, cutoff)
        raw_store = _raw_store_part(
            driver,
            raw_path,
            cutoff,
            retention,
            config.batch_size,
            chain(_uuid_chunks(session, _POST_UUIDS, cutoff), [parent_ref_uuids]),
        )

    kept_parent_refs = int(kept.get("parent_refs", 0)) - len(parent_ref_uuids)
    return {
        "cutoff": audit_time(cutoff),
        "config": dict(describe_config(retention, config)),
        "delete": {
            "posts": {
                "total": int(posts.get("total", 0)) + len(parent_ref_uuids),
                **{
                    kind: int(posts.get(kind, 0)) + sum(1 for _, ref_kind in parent_refs if ref_kind == kind)
                    for kind in ("postings", "comments", "messages")
                },
                "expired": int(posts.get("expired", 0)),
                "cascaded": int(posts.get("total", 0)) - int(posts.get("expired", 0)),
                "parent_refs": len(parent_ref_uuids),
                "deadline_earliest": _when(posts.get("deadline_earliest")),
                "deadline_latest": _when(posts.get("deadline_latest")),
                "ingested_oldest": _when(posts.get("ingested_oldest")),
                "ingested_newest": _when(posts.get("ingested_newest")),
                "ingested_days_ago_max": _days_before(cutoff, posts.get("ingested_oldest")),
                "ingested_days_ago_min": _days_before(cutoff, posts.get("ingested_newest")),
            },
            "aliases": int(linked.get("aliases", 0)),
            "hashtags": int(linked.get("hashtags", 0)),
            "attachments": int(linked.get("attachments", 0)),
            "raw_store": raw_store,
            "uploads": _uploads_part(home, cutoff, retention),
        },
        "keep": {
            "posts": {
                "total": int(kept.get("total", 0)) - len(parent_ref_uuids),
                "not_due": int(kept.get("not_due", 0)),
                "next_deadline": _when(kept.get("next_deadline")),
                "without_deadline": {
                    "retention_off": int(kept.get("retention_off", 0)),
                    "before_ingestion_times": int(kept.get("before_ingestion_times", 0)),
                    "parent_refs": kept_parent_refs,
                    "unfilled": int(kept.get("unfilled", 0)),
                },
            },
            "authors": {**{key: int(value) for key, value in authors.items()}, "already": already},
            "entities_left_without_alias": int(entities.get("entities", 0)),
            "groups_left_without_post": int(groups.get("groups", 0)),
        },
    }


def _counts(mapping: Mapping[str, Any], keys: Sequence[str]) -> str:
    """Render ``key n`` pairs.

    Args:
        mapping: Counts by key.
        keys: The keys to show, in order.

    Returns:
        E.g. ``postings 3, comments 2, messages 0``.
    """
    return ", ".join(f"{key} {mapping.get(key, 0)}" for key in keys)


def _span(earliest: str | None, latest: str | None) -> str:
    """Render a time range by date.

    Args:
        earliest: An ISO timestamp or ``None``.
        latest: An ISO timestamp or ``None``.

    Returns:
        ``YYYY-MM-DD … YYYY-MM-DD``, or ``-`` when there is none.
    """
    if earliest is None or latest is None:
        return "-"
    return f"{earliest[:10]} … {latest[:10]}"


def render_report(report: Mapping[str, Any]) -> str:
    """Render a report for a terminal.

    Args:
        report: :func:`build_report`'s result.

    Returns:
        The report as text lines.
    """
    cfg = report["config"]
    delete = report["delete"]
    posts = delete["posts"]
    raw = delete["raw_store"]
    uploads = delete["uploads"]
    keep = report["keep"]
    kept = keep["posts"]
    authors = keep["authors"]
    kinds = ("postings", "comments", "messages")
    if not cfg["sweep_enabled"]:
        sweeper = "off (RETENTION_SWEEP_ENABLED): nothing is deleted until it is switched on"
    elif cfg["interval_s"]:
        sweeper = f"on, every {cfg['interval_s']:g} s"
    else:
        sweeper = "on, one-off runs only (RETENTION_SWEEP_INTERVAL_S=0)"
    lines = [
        f"Retention dry run at {report['cutoff']} (nothing is recorded or deleted).",
        f"Sweeper: {sweeper}.",
        f"Retention: {'on' if cfg['retention_enabled'] else 'off'}, {cfg['period_days']} days; "
        f"batches of {cfg['batch_size']} posts.",
        "",
        "A sweep now would delete:",
        f"  posts: {posts['total']} ({_counts(posts, kinds)})",
        f"    expired: {posts['expired']}, deadlines {_span(posts['deadline_earliest'], posts['deadline_latest'])}",
        f"    with an expired post (comments, replies): {posts['cascaded']}",
        f"    bare parent references: {posts['parent_refs']}",
        f"    ingested {_span(posts['ingested_oldest'], posts['ingested_newest'])}"
        + (
            f" ({posts['ingested_days_ago_min']} to {posts['ingested_days_ago_max']} days ago)"
            if posts["ingested_days_ago_max"] is not None
            else ""
        ),
        f"  only they reference: aliases {delete['aliases']}, hashtags {delete['hashtags']}, "
        f"attachments {delete['attachments']}",
        f"  their raw-store rows: {_counts(raw['of_deleted_posts'], kinds)}",
    ]
    if raw["without_post"] is None:
        lines.append("  raw-store rows no post holds: none (retention is off)")
    else:
        rows = raw["without_post"]
        lines.append(
            f"  raw-store rows no post holds, received before {raw.get('without_post_received_before', '-')[:10]}: "
            f"{_counts(rows, kinds)}; received {_span(rows['received_oldest'], rows['received_newest'])}"
        )
    if uploads is None:
        lines.append("  staged uploads: none (retention is off)")
    else:
        lines.append(
            f"  staged uploads untouched since before {uploads['received_before'][:10]}: "
            f"{uploads['directories']} directories, {uploads['files']} files"
            + (f", oldest {uploads['oldest'][:10]}" if uploads["oldest"] else "")
        )
    without = kept["without_deadline"]
    lines += [
        "",
        "Kept:",
        f"  posts: {kept['total']}; not yet due {kept['not_due']}"
        + (f" (next deadline {kept['next_deadline'][:10]})" if kept["next_deadline"] else ""),
        f"    without a deadline: ingested with retention off {without['retention_off']}, "
        f"ingested before ingestion times were recorded {without['before_ingestion_times']}, "
        f"bare parent references {without['parent_refs']}"
        + (
            f" ({without['unfilled']} of them with an author: their own row was never applied; "
            "re-ingest it to give them a deadline)"
            if without["unfilled"]
            else ""
        ),
        f"  raw-store author rows: {_counts(raw['kept_author_rows'], ('profiles', 'connections'))}",
        "  authors (kept indefinitely, ADR 0006):",
        f"    of the deleted posts: {authors['of_deleted_posts']}",
        f"    left without any post: {authors['left_without_posts']} "
        f"(with social edges only {authors['left_with_social_edges_only']}, "
        f"without any post or social edge {authors['left_without_posts_or_social_edges']}, "
        f"{authors['of_those_with_profile_data']} of them with profile data)",
        f"    without any post or social edge already: {authors['already']['without_posts_or_social_edges']} "
        f"({authors['already']['of_those_with_profile_data']} with profile data)",
        f"  entities left without an alias: {keep['entities_left_without_alias']}",
        f"  groups left without a post: {keep['groups_left_without_post']}",
    ]
    return "\n".join(lines)
