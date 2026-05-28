"""Concrete upstream adapter that reads from local CSV dumps.

The vendor delivers each table as one or more CSV files dropped onto
the host; chorus reads them from a configured source directory. This
is the airgap-compatible counterpart to a future network adapter.

Rows are yielded as ``dict[str, str]`` keyed by the *exact* upstream
column names (e.g. ``"UUID"``, ``"Text Content"``, ``"Author ID"``,
``"Crawled at"``). Per-table ``from_row`` functions in the sibling
modules consume those keys unchanged; this adapter performs no
renaming or type coercion.

Each table kind accepts the legacy single-file basename
(``postings.csv``, ``comments.csv``, etc.) and segmented exports whose
basenames end in ``_<table>.csv`` (for example,
``2026-05_connections.csv``). All matching files for a table kind are
read in deterministic name order.

The ``since`` filter reads ``"Crawled at"`` for postings, comments,
profiles, and connections, and ``"Timestamp"`` for messages (the
messages table has no ``Crawled at`` column). When the cell is missing
or unparseable the row is *kept* rather than dropped — silent loss is
worse than over-inclusion.
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

    Reads one or more CSVs per table kind from ``source_dir`` using the
    legacy basename (for example ``connections.csv``) and segmented
    ``*_<table>.csv`` exports.

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
        """Yield posting rows from all matching postings table files.

        Args:
            since: Restrict to rows whose ``Crawled at`` is after this
                cutoff. ``None`` means full backfill.

        Returns:
            Iterable of raw upstream posting rows, keys verbatim.
        """
        return self._read_table("postings", since=since, since_column="Crawled at")

    def fetch_comments(self, since: datetime | None) -> Iterable[dict[str, Any]]:
        """Yield comment rows from all matching comments table files.

        Args:
            since: Restrict to rows whose ``Crawled at`` is after this
                cutoff. ``None`` means full backfill.

        Returns:
            Iterable of raw upstream comment rows, keys verbatim.
        """
        return self._read_table("comments", since=since, since_column="Crawled at")

    def fetch_messages(self, since: datetime | None) -> Iterable[dict[str, Any]]:
        """Yield chat-message rows from all matching messages table files.

        The messages table has no ``Crawled at`` column; the since
        filter reads ``Timestamp`` instead.

        Args:
            since: Restrict to rows whose ``Timestamp`` is after this
                cutoff. ``None`` means full backfill.

        Returns:
            Iterable of raw upstream message rows, keys verbatim.
        """
        return self._read_table("messages", since=since, since_column="Timestamp")

    def fetch_profiles(self, since: datetime | None) -> Iterable[dict[str, Any]]:
        """Yield author-profile rows from all matching profiles table files.

        Args:
            since: Restrict to rows whose ``Crawled at`` is after this
                cutoff. ``None`` means full backfill.

        Returns:
            Iterable of raw upstream profile rows, keys verbatim.
        """
        return self._read_table("profiles", since=since, since_column="Crawled at")

    def fetch_connections(self, since: datetime | None) -> Iterable[dict[str, Any]]:
        """Yield social-graph edge rows from all matching connections files.

        Args:
            since: Restrict to rows whose ``Crawled at`` is after this
                cutoff. ``None`` means full backfill.

        Returns:
            Iterable of raw upstream connection rows, keys verbatim.
        """
        return self._read_table("connections", since=since, since_column="Crawled at")

    def _read_table(
        self,
        table_name: str,
        *,
        since: datetime | None,
        since_column: str,
    ) -> Iterator[dict[str, Any]]:
        """Read and filter all files that belong to one table kind.

        Args:
            table_name: Table kind suffix such as ``"connections"``.
            since: Cutoff for the since filter; ``None`` disables filtering.
            since_column: Column whose value is parsed as the row's
                timestamp for the since filter.

        Yields:
            One ``dict`` per CSV row, keys taken verbatim from the
            header line. Rows whose ``since_column`` value can be
            parsed and is not after ``since`` are skipped; rows
            without a parseable value are always yielded.
        """
        paths = self._table_paths(table_name)
        if not paths:
            logger.warning(
                "ingestion source files missing for table {} under {}",
                table_name,
                self.source_dir,
            )
            return
        for path in paths:
            yield from self._read_file(path, since=since, since_column=since_column)

    def _table_paths(self, table_name: str) -> tuple[Path, ...]:
        """Return all source files for one table kind in deterministic order.

        Args:
            table_name: Table kind suffix such as ``"connections"``.

        Returns:
            Tuple of paths to all files that match the legacy basename or
            segmented export pattern for the given table kind, in deterministic name
            order. If no files match, returns an empty tuple.
        """
        legacy_path = self.source_dir / f"{table_name}.csv"
        paths: list[Path] = []
        if legacy_path.is_file():
            paths.append(legacy_path)

        segmented_paths = sorted(
            path for path in self.source_dir.glob(f"*_{table_name}.csv") if path.is_file() and path != legacy_path
        )
        paths.extend(segmented_paths)
        return tuple(paths)

    def _read_file(
        self,
        path: Path,
        *,
        since: datetime | None,
        since_column: str,
    ) -> Iterator[dict[str, Any]]:
        """Read and filter one CSV file."""
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
                dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(f, dialect=dialect)
            for row in reader:
                # csv.DictReader puts fields past the header width under
                # a None key (default restkey). That trips json.dumps(
                # sort_keys=True) downstream — None vs str is unorderable
                # — and the overflow isn't meaningful since we have no
                # header to label it. Drop it at the boundary.
                row.pop(None, None)
                if since is not None and not self._after_cutoff(row, since_column, since):
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
