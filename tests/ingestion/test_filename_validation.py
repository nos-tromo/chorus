"""Tests for upload filename → upstream table recognition.

The frontend ingestion endpoint validates uploaded filenames with the same
rule the file adapter uses to pick up files, so a mis-named upload is rejected
loudly (422) instead of being silently ignored and producing a green "0 rows"
run. See ADR 0014.
"""

from __future__ import annotations

import pytest

from chorus.ingestion.upstream import TABLES, table_for_filename


def test_tables_constant_lists_the_five_upstream_tables() -> None:
    """The shared constant is the single source of truth for table names."""
    assert TABLES == ("postings", "comments", "messages", "profiles", "connections")


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("postings.csv", "postings"),
        ("comments.csv", "comments"),
        ("messages.csv", "messages"),
        ("profiles.csv", "profiles"),
        ("connections.csv", "connections"),
    ],
)
def test_legacy_basenames_are_recognized(filename: str, expected: str) -> None:
    """The legacy single-file basenames map to their table kind."""
    assert table_for_filename(filename) == expected


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("2026-05_connections.csv", "connections"),
        ("part1_postings.csv", "postings"),
        ("export_2026_messages.csv", "messages"),
    ],
)
def test_segmented_exports_are_recognized(filename: str, expected: str) -> None:
    """``*_<table>.csv`` segmented exports map to their table kind."""
    assert table_for_filename(filename) == expected


@pytest.mark.parametrize(
    "filename",
    [
        "data.csv",  # unknown stem
        "postings.txt",  # wrong extension
        "postingsX.csv",  # stem is not exactly a table and has no _<table> suffix
        "postings",  # no extension
        "Postings.csv",  # case-sensitive: mirrors the adapter's glob on Linux
        "",  # empty
    ],
)
def test_unrecognized_names_return_none(filename: str) -> None:
    """Anything the adapter would ignore is rejected (returns ``None``)."""
    assert table_for_filename(filename) is None


@pytest.mark.parametrize(
    "filename",
    [
        "../postings.csv",
        "sub/postings.csv",
        "sub\\postings.csv",
        "..\\postings.csv",
    ],
)
def test_path_traversal_is_rejected(filename: str) -> None:
    """Filenames carrying a path or ``..`` are rejected outright."""
    assert table_for_filename(filename) is None
