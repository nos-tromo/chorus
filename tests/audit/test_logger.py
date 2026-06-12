"""AuditLogger persistence — focused unit tests."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from chorus.audit.logger import AuditRecord


def test_record_stores_non_ascii_as_utf8(in_memory_audit: Any) -> None:
    r"""Audit params/entities keep non-ASCII as readable UTF-8, not ``\uXXXX``.

    The audit log is read by humans during §76 compliance review; escaping a
    name such as ``محمد`` to ``\uXXXX`` makes it unreadable while SQLite stores
    UTF-8 either way. Both JSON columns must carry the real characters and still
    round-trip through ``json.loads``. A raw Arabic substring being present in
    the stored text is itself proof it was not ASCII-escaped.
    """
    entity = "محمد"
    record = AuditRecord(
        user="analyst",
        tool_name="posts_mentioning",
        params={"entity": entity},
        entities_touched=[entity],
    )
    in_memory_audit.record(record)

    params_json, entities_json = (
        sqlite3.connect(in_memory_audit.db_path)
        .execute("SELECT params_json, entities_touched_json FROM audit_log ORDER BY id DESC LIMIT 1")
        .fetchone()
    )

    assert entity in params_json
    assert entity in entities_json
    assert json.loads(params_json)["entity"] == entity
    assert json.loads(entities_json) == [entity]
