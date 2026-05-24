"""Concrete upstream adapter that reads from local CSV dumps.

The vendor delivers each table as a CSV file dropped onto the host;
chorus reads them from a configured source directory. This is the
airgap-compatible counterpart to a future network adapter.

Rows are yielded as ``dict[str, str]`` keyed by the *exact* upstream
column names (e.g. ``"UUID"``, ``"Text Content"``, ``"Author ID"``,
``"Crawled at"``). Per-table ``from_row`` functions in the sibling
modules consume those keys unchanged; this adapter performs no
renaming or type coercion.

The ``since`` filter reads ``"Crawled at"`` for postings, comments,
and profiles, and ``"Timestamp"`` for messages (the messages table
has no ``Crawled at`` column). When the cell is missing or
unparseable the row is *kept* rather than dropped — silent loss is
worse than over-inclusion.

Connections ingestion is still blocked on the upstream schema and
raises ``NotImplementedError`` (see ADR 0002).
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


class FileUpstreamAdapter:
    """File-backed implementation of :class:`UpstreamAdapter`.

    Reads one CSV per table from ``source_dir``:
    ``postings.csv``, ``comments.csv``, ``messages.csv``,
    ``profiles.csv``. Connections is left as a raising stub until the
    upstream schema is pinned (ADR 0002).

    Attributes:
        source_dir: Directory containing the per-table CSV dumps.
    """

    def __init__(self, source_dir: Path) -> None:
        """Bind the adapter to a source directory.

        Args:
            source_dir: Directory containing per-table CSV dumps. The
                directory does not have to exist or be populated;
                missing files cause the corresponding ``fetch_*`` to
                yield nothing.
        """
        self.source_dir = source_dir

    def fetch_postings(self, since: datetime | None) -> Iterable[dict[str, Any]]:
        """Yield posting rows from ``postings.csv``.

        Args:
            since: Restrict to rows whose ``Crawled at`` is after this
                cutoff. ``None`` means full backfill.

        Returns:
            Iterable of raw upstream posting rows, keys verbatim.
        """
        return self._read("postings.csv", since=since, since_column="Crawled at")

    def fetch_comments(self, since: datetime | None) -> Iterable[dict[str, Any]]:
        """Yield comment rows from ``comments.csv``.

        Args:
            since: Restrict to rows whose ``Crawled at`` is after this
                cutoff. ``None`` means full backfill.

        Returns:
            Iterable of raw upstream comment rows, keys verbatim.
        """
        return self._read("comments.csv", since=since, since_column="Crawled at")

    def fetch_messages(self, since: datetime | None) -> Iterable[dict[str, Any]]:
        """Yield chat-message rows from ``messages.csv``.

        The messages table has no ``Crawled at`` column; the since
        filter reads ``Timestamp`` instead.

        Args:
            since: Restrict to rows whose ``Timestamp`` is after this
                cutoff. ``None`` means full backfill.

        Returns:
            Iterable of raw upstream message rows, keys verbatim.
        """
        return self._read("messages.csv", since=since, since_column="Timestamp")

    def fetch_profiles(self, since: datetime | None) -> Iterable[dict[str, Any]]:
        """Yield author-profile rows from ``profiles.csv``.

        Args:
            since: Restrict to rows whose ``Crawled at`` is after this
                cutoff. ``None`` means full backfill.

        Returns:
            Iterable of raw upstream profile rows, keys verbatim.
        """
        return self._read("profiles.csv", since=since, since_column="Crawled at")

    def fetch_connections(self, since: datetime | None) -> Iterable[dict[str, Any]]:
        """Raise — connections ingestion is blocked on the upstream schema.

        Args:
            since: Ignored.

        Raises:
            NotImplementedError: Always; see ADR 0002.
        """
        raise NotImplementedError(
            "Connections ingestion is blocked on upstream schema — see ADR 0002."
        )

    def _read(
        self,
        filename: str,
        *,
        since: datetime | None,
        since_column: str,
    ) -> Iterator[dict[str, Any]]:
        """Read and filter a single table file.

        Args:
            filename: CSV basename inside ``source_dir``.
            since: Cutoff for the since filter; ``None`` disables filtering.
            since_column: Column whose value is parsed as the row's
                timestamp for the since filter.

        Yields:
            One ``dict`` per CSV row, keys taken verbatim from the
            header line. Rows whose ``since_column`` value can be
            parsed and is not after ``since`` are skipped; rows
            without a parseable value are always yielded.
        """
        path = self.source_dir / filename
        if not path.exists():
            logger.warning("ingestion source file missing: {}", path)
            return
        # ``utf-8-sig`` transparently strips a leading BOM if present —
        # Excel-style exports often include one, which would otherwise
        # contaminate the first header (e.g. ``"﻿UUID"``) and
        # cause KeyError on the first column lookup downstream.
        with path.open("r", newline="", encoding="utf-8-sig") as f:
            # Sniff the delimiter from the first few KiB. Upstream
            # exports occasionally use semicolons (German/European
            # convention) rather than commas; we restrict the candidate
            # set to the common single-char delimiters so the sniffer
            # doesn't latch onto something unexpected. If sniffing
            # fails (very short file, etc.) fall back to standard
            # comma-separated.
            sample = f.read(8192)
            f.seek(0)
            try:
                dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(
                    sample, delimiters=",;\t|"
                )
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(f, dialect=dialect)
            for row in reader:
                # csv.DictReader puts fields past the header width under
                # a None key (default restkey). That trips json.dumps(
                # sort_keys=True) downstream — None vs str is unorderable
                # — and the overflow isn't meaningful since we have no
                # header to label it. Drop it at the boundary.
                row.pop(None, None)  # type: ignore[call-overload]
                if since is not None and not self._after_cutoff(
                    row, since_column, since
                ):
                    continue
                yield row

    @staticmethod
    def _after_cutoff(row: dict[str, Any], column: str, since: datetime) -> bool:
        """Return whether the row's timestamp column is after ``since``.

        Over-includes when the value is missing or unparseable: the
        only false return is for a successfully parsed timestamp that
        sits at or before the cutoff.

        Args:
            row: One CSV row.
            column: Column to consult.
            since: Cutoff timestamp.

        Returns:
            ``True`` if the value parses and is strictly after
            ``since``; ``True`` if it does not parse; ``False`` only
            for parseable values at or before the cutoff.
        """
        raw = row.get(column)
        if not raw or not raw.strip():
            return True
        try:
            ts = datetime.fromisoformat(raw.strip())
        except ValueError:
            return True
        return ts > since
