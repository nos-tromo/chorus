"""Idempotent Cypher migration runner.

Each file in this directory matching `NNN_*.cypher` is one migration.
Statements are split on bare `;` (string-literal-aware), executed in
auto-commit (Neo4j refuses DDL inside an explicit transaction), and the
version is recorded as `(:_Migration {version})` on success.

Templating: any `${VAR}` in a migration body is substituted from the env
substitution map below before execution. Currently the only key is
`EMBED_DIM` from `InferenceConfig`.

Audit trail: every applied migration writes one ``migration.apply`` event,
``admin`` for a schema-only migration (constraints and indexes) and
``modify`` for one with any other statement, which can change stored data.
The event carries the version and the store's change counters.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from string import Template

from neo4j import Driver

from chorus.audit.trail import AuditContext
from chorus.utils.audit_event import Target
from chorus.utils.env_cfg import load_inference_env

#: The audit operation of one applied migration.
MIGRATION_OPERATION = "migration.apply"

# Schema DDL: creating or dropping a constraint or an index of any kind.
_SCHEMA_STATEMENT = re.compile(
    r"^(?:CREATE(?:\s+OR\s+REPLACE)?|DROP)\s+"
    r"(?:(?:RANGE|TEXT|POINT|VECTOR|FULLTEXT|LOOKUP|BTREE)\s+)?(?:INDEX|CONSTRAINT)\b",
    re.IGNORECASE,
)
_COUNTERS = (
    "nodes_created",
    "nodes_deleted",
    "relationships_created",
    "relationships_deleted",
    "properties_set",
    "labels_added",
    "labels_removed",
    "indexes_added",
    "indexes_removed",
    "constraints_added",
    "constraints_removed",
)


@dataclass(frozen=True)
class Migration:
    """One migration file, ready to apply.

    Attributes:
        version: The file stem, e.g. ``"001_constraints"``.
        statements: The statements, after ``${VAR}`` substitution.
        data_changing: Whether any statement is something other than schema
            DDL, so the migration can change stored data.
    """

    version: str
    statements: tuple[str, ...]
    data_changing: bool

    @property
    def action(self) -> str:
        """The audit action applying it is: ``modify`` or ``admin``."""
        return "modify" if self.data_changing else "admin"


MIGRATION_DIR = Path(__file__).resolve().parent
_FILE_RE = re.compile(r"^(\d{3,})_[\w\-]+\.cypher$")


def _substitution_map() -> dict[str, str]:
    """Build the ``${VAR}`` substitution map applied to every migration body.

    Returns:
        Mapping of template variable names to their substituted string
        values. Currently exposes ``EMBED_DIM`` from the active
        inference config so vector indexes can be sized at apply time.
    """
    cfg = load_inference_env()
    return {"EMBED_DIM": str(cfg.embed_dim)}


def _split_statements(body: str) -> list[str]:
    r"""Split a Cypher migration body into individual statements.

    Splits on ``;`` at statement boundaries while respecting string
    literals (``'``, ``"``, ``\\``) and stripping ``//`` line comments.
    Cypher DDL statements rarely contain semicolons inside literals;
    this implementation handles the simple case rather than maintaining
    a full Cypher parser.

    Args:
        body: Raw text of the migration file, after env substitution.

    Returns:
        Cleaned statements in source order, with empty statements
        discarded.
    """
    stripped_lines: list[str] = []
    for line in body.splitlines():
        # remove `//` line comments (Cypher); preserve content before them
        in_str: str | None = None
        out_chars: list[str] = []
        i = 0
        while i < len(line):
            ch = line[i]
            if in_str:
                out_chars.append(ch)
                if ch == in_str and (i == 0 or line[i - 1] != "\\"):
                    in_str = None
            else:
                if ch in ("'", '"', "`"):
                    in_str = ch
                    out_chars.append(ch)
                elif ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
                    break  # comment to end of line
                else:
                    out_chars.append(ch)
            i += 1
        stripped_lines.append("".join(out_chars))
    body = "\n".join(stripped_lines)

    parts: list[str] = []
    buf: list[str] = []
    in_str = None
    for ch in body:
        if in_str:
            buf.append(ch)
            if ch == in_str:
                in_str = None
        else:
            if ch in ("'", '"', "`"):
                in_str = ch
                buf.append(ch)
            elif ch == ";":
                stmt = "".join(buf).strip()
                if stmt:
                    parts.append(stmt)
                buf = []
            else:
                buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _discover() -> list[tuple[str, Path]]:
    """Discover migration files in lexicographic version order.

    Only files matching ``NNN_*.cypher`` (three or more leading digits,
    followed by a name segment) are returned; everything else under the
    migrations directory is ignored.

    Returns:
        ``(version, path)`` pairs sorted by version. ``version`` is the
        file stem (e.g. ``"001_init"``).
    """
    discovered: list[tuple[str, Path]] = []
    for p in MIGRATION_DIR.iterdir():
        m = _FILE_RE.match(p.name)
        if m:
            discovered.append((p.stem, p))
    discovered.sort(key=lambda pair: pair[0])
    return discovered


def _load(version: str, path: Path, subs: dict[str, str]) -> Migration:
    """Read, substitute and split one migration file.

    Args:
        version: The file stem.
        path: The migration file.
        subs: The ``${VAR}`` substitution map.

    Returns:
        The :class:`Migration`.
    """
    body = Template(path.read_text(encoding="utf-8")).safe_substitute(subs)
    statements = tuple(_split_statements(body))
    data_changing = any(_SCHEMA_STATEMENT.match(stmt) is None for stmt in statements)
    return Migration(version=version, statements=statements, data_changing=data_changing)


def pending_migrations(driver: Driver) -> list[Migration]:
    """Return the migrations not applied yet, loaded, in apply order.

    Args:
        driver: Open Neo4j driver to query for applied versions.

    Returns:
        The pending :class:`Migration` objects; empty when up to date.
    """
    subs = _substitution_map()
    applied = applied_versions(driver)
    return [_load(version, path, subs) for version, path in _discover() if version not in applied]


def migration_actions(migrations: Sequence[Migration]) -> tuple[str, ...]:
    """Return the audit actions applying ``migrations`` performs.

    Args:
        migrations: The migrations about to be applied.

    Returns:
        ``("admin",)``, plus ``"modify"`` when any of them is data-changing.
    """
    return ("admin", "modify") if any(m.data_changing for m in migrations) else ("admin",)


def applied_versions(driver: Driver) -> set[str]:
    """Return the set of migration versions already recorded in Neo4j.

    Args:
        driver: Open Neo4j driver to query.

    Returns:
        Versions found on ``:_Migration`` nodes. Empty on a fresh
        database.
    """
    with driver.session() as s:
        result = s.run("MATCH (m:_Migration) RETURN m.version AS v")
        return {row["v"] for row in result}


def pending_versions(driver: Driver) -> list[str]:
    """Return discovered migrations not yet applied, in apply order.

    Args:
        driver: Open Neo4j driver to query for applied versions.

    Returns:
        Versions present on disk (``NNN_*.cypher``) but absent from the
        ``:_Migration`` nodes, in the same lexicographic order
        :func:`apply_all` would apply them. Empty when up to date.
    """
    applied = applied_versions(driver)
    return [version for version, _ in _discover() if version not in applied]


def apply_all(driver: Driver, *, audit: AuditContext, only: Iterable[str] | None = None) -> list[str]:
    """Apply pending migrations and record them on ``:_Migration`` nodes.

    Migrations already recorded in the graph are skipped. Each statement
    is executed in auto-commit mode because Neo4j refuses DDL inside an
    explicit transaction. Each applied migration writes one
    ``migration.apply`` audit event once it is recorded; a migration that
    fails writes it with ``outcome="error"`` and the counters so far, and
    stops the run.

    Args:
        driver: Open Neo4j driver.
        audit: The caller's audit context: the system at startup, the CLI
            operator, or the UI user.
        only: If provided, restrict application to versions in this
            collection. Versions outside this set are still considered
            for skipping if already applied, but are never freshly
            applied.

    Returns:
        Versions newly applied on this call, in apply order. Empty when
        the database is already up to date.

    Raises:
        AuditFailure: If a migration's event could not be written.
    """
    target = set(only) if only is not None else None
    newly: list[str] = []
    for migration in pending_migrations(driver):
        if target is not None and migration.version not in target:
            continue
        changes = dict.fromkeys(_COUNTERS, 0)
        try:
            with driver.session() as s:
                for stmt in migration.statements:
                    counters = s.run(stmt).consume().counters
                    for name in _COUNTERS:
                        changes[name] += int(getattr(counters, name, 0) or 0)
                s.run(
                    "MERGE (m:_Migration {version: $v}) SET m.applied_at = $ts",
                    v=migration.version,
                    ts=datetime.now(UTC).isoformat(timespec="milliseconds"),
                ).consume()
        except Exception:
            _record(audit, migration, changes, outcome="error")
            raise
        _record(audit, migration, changes, outcome="ok")
        newly.append(migration.version)
    return newly


def _record(audit: AuditContext, migration: Migration, changes: dict[str, int], *, outcome: str) -> str:
    """Write one migration's ``migration.apply`` event.

    Args:
        audit: The caller's audit context.
        migration: The migration applied (or attempted).
        changes: The store's change counters over its statements.
        outcome: ``ok`` or ``error``.

    Returns:
        The event's id.
    """
    return audit.emit(
        migration.action,
        MIGRATION_OPERATION,
        outcome=outcome,
        target=Target(kind="migration", ids=[migration.version], count=1),
        criteria={
            "statements": len(migration.statements),
            "changes": {name: count for name, count in changes.items() if count},
        },
    )
