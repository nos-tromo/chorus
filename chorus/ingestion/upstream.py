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
from typing import Any, Final

from loguru import logger

#: The five upstream tables chorus ingests, in stage order. Single source of
#: truth for table names; reused by :func:`table_for_filename` and the
#: frontend ingestion endpoint's upload validation (ADR 0014).
TABLES: Final[tuple[str, ...]] = ("postings", "comments", "messages", "profiles", "connections")

_CSV_SUFFIX: Final = ".csv"


def table_for_filename(filename: str) -> str | None:
    """Return the upstream table a filename belongs to, or ``None``.

    Mirrors the recognition rule in
    :meth:`FileUpstreamAdapter._table_paths`: a file belongs to table ``t``
    when its basename is exactly ``"<t>.csv"`` (legacy single-file export) or
    ends with ``"_<t>.csv"`` (segmented export, e.g.
    ``"2026-05_connections.csv"``). Matching is case-sensitive, matching the
    adapter's ``glob`` on case-sensitive filesystems (the production image is
    Linux). Filenames carrying a path separator or ``".."`` are rejected.

    Keep this in lockstep with ``_table_paths`` — the two are independent
    implementations of one rule (string match here, directory glob there); the
    coupling is recorded in ADR 0014.

    Args:
        filename: The uploaded file's basename (or any candidate name).

    Returns:
        The matching table name from :data:`TABLES`, or ``None`` when the
        name is one the adapter would not pick up.
    """
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    if not filename.endswith(_CSV_SUFFIX):
        return None
    stem = filename[: -len(_CSV_SUFFIX)]
    for table in TABLES:
        if stem == table or stem.endswith(f"_{table}"):
            return table
    return None


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
            # Sniff the DELIMITER ONLY, then pin the quoting policy. Quote
            # handling must not be sniffed: csv.Sniffer infers ``doublequote``
            # from this head sample, and when the leading rows happen to carry
            # no doubled-quote escape it guesses ``doublequote=False``. A later
            # field that *does* escape a literal quote as ``""`` (RFC 4180 /
            # Excel, which is what the upstream emits) is then mis-parsed: the
            # first quote of the pair is read as closing the field, an embedded
            # newline becomes a record terminator, and the multi-line post
            # shatters into a phantom short row whose missing trailing columns
            # (e.g. the required ``Network``) default to ``None``. The upstream
            # is a single, known source that always quotes with ``"`` and
            # escapes by doubling, so we fix that policy rather than guess it.
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
            except csv.Error:
                delimiter = ","
            reader = csv.DictReader(f, delimiter=delimiter, quotechar='"', doublequote=True)
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
