"""Idempotent Cypher migration runner.

Each file in this directory matching `NNN_*.cypher` is one migration.
Statements are split on bare `;` (string-literal-aware), executed in
auto-commit (Neo4j refuses DDL inside an explicit transaction), and the
version is recorded as `(:_Migration {version})` on success.

Templating: any `${VAR}` in a migration body is substituted from the env
substitution map below before execution. Currently the only key is
`EMBED_DIM` from `InferenceConfig`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from string import Template

from neo4j import Driver

from chorus.utils.env_cfg import load_inference_env

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
    stripped_lines = []
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


def apply_all(driver: Driver, *, only: Iterable[str] | None = None) -> list[str]:
    """Apply pending migrations and record them on ``:_Migration`` nodes.

    Migrations already recorded in the graph are skipped. Each statement
    is executed in auto-commit mode because Neo4j refuses DDL inside an
    explicit transaction.

    Args:
        driver: Open Neo4j driver.
        only: If provided, restrict application to versions in this
            collection. Versions outside this set are still considered
            for skipping if already applied, but are never freshly
            applied.

    Returns:
        Versions newly applied on this call, in apply order. Empty when
        the database is already up to date.
    """
    subs = _substitution_map()
    applied = applied_versions(driver)
    target = set(only) if only is not None else None
    newly: list[str] = []
    for version, path in _discover():
        if target is not None and version not in target:
            continue
        if version in applied:
            continue
        body = Template(path.read_text(encoding="utf-8")).safe_substitute(subs)
        statements = _split_statements(body)
        with driver.session() as s:
            for stmt in statements:
                s.run(stmt)
            s.run(
                "MERGE (m:_Migration {version: $v}) SET m.applied_at = $ts",
                v=version,
                ts=datetime.now(UTC).isoformat(timespec="milliseconds"),
            )
        newly.append(version)
    return newly
