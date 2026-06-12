"""RawStore persistence — focused unit tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from chorus.ingestion.raw_store import RawStore


def test_write_batch_stores_non_ascii_as_utf8(tmp_path: Path) -> None:
    r"""Raw payloads keep non-ASCII as readable UTF-8, not ``\uXXXX`` escapes.

    The raw store is the "what did the upstream tell us, exactly" record and is
    inspected directly during re-ingestion debugging. Escaping Arabic post text
    to ``\uXXXX`` only makes it unreadable; SQLite stores UTF-8 regardless. The
    stored payload must carry the real characters and round-trip through
    ``json.loads``. A raw Arabic substring being present is itself proof it was
    not ASCII-escaped.
    """
    store = RawStore(tmp_path / "raw.sqlite")
    store.init_schema()

    text = "مرحبا بالعالم"
    written = store.write_batch("postings", [{"UUID": "p-1", "Text Content": text}])
    assert written == 1

    (payload_json,) = (
        sqlite3.connect(store.db_path).execute("SELECT payload_json FROM raw_rows WHERE uuid = ?", ("p-1",)).fetchone()
    )

    assert text in payload_json
    assert json.loads(payload_json)["Text Content"] == text
