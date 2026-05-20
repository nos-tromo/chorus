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
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Iterable

from neo4j import Driver

from chorus.utils.env_cfg import load_inference_env


MIGRATION_DIR = Path(__file__).resolve().parent
_FILE_RE = re.compile(r"^(\d{3,})_[\w\-]+\.cypher$")


def _substitution_map() -> dict[str, str]:
    cfg = load_inference_env()
    return {"EMBED_DIM": str(cfg.embed_dim)}


def _split_statements(body: str) -> list[str]:
    """Split on `;` at statement boundaries, respecting string literals and
    line comments. Cypher DDL statements rarely contain semicolons inside
    literals; this implementation handles the simple case and treats
    `//`-to-EOL as comments to strip."""
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
    discovered: list[tuple[str, Path]] = []
    for p in MIGRATION_DIR.iterdir():
        m = _FILE_RE.match(p.name)
        if m:
            discovered.append((p.stem, p))
    discovered.sort(key=lambda pair: pair[0])
    return discovered


def applied_versions(driver: Driver) -> set[str]:
    with driver.session() as s:
        result = s.run("MATCH (m:_Migration) RETURN m.version AS v")
        return {row["v"] for row in result}


def apply_all(driver: Driver, *, only: Iterable[str] | None = None) -> list[str]:
    """Apply migrations not yet recorded. Returns the list of versions applied
    on this call (empty if up to date)."""
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
                ts=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            )
        newly.append(version)
    return newly
