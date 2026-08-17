"""Per-project driver registry tests (ADR 0017)."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_get_driver_default_project_connects(chorus_env: Path) -> None:
    """``get_driver("default")`` reaches the compat-mode instance."""
    from chorus.db.registry import close_all_drivers, get_driver

    try:
        with get_driver("default").session() as s:
            assert s.run("RETURN 1 AS one").single()["one"] == 1
    finally:
        close_all_drivers()


def test_get_driver_caches_per_project(chorus_env: Path) -> None:
    """Repeated lookups return the same pooled driver object."""
    from chorus.db.registry import close_all_drivers, get_driver

    try:
        assert get_driver("default") is get_driver("default")
    finally:
        close_all_drivers()


def test_get_driver_unknown_project_raises(chorus_env: Path) -> None:
    """A project outside the configured set is rejected, never lazily created."""
    from chorus.db.registry import UnknownProjectError, get_driver

    with pytest.raises(UnknownProjectError):
        get_driver("nope")


def test_two_projects_yield_distinct_drivers(
    chorus_env: Path, monkeypatch: pytest.MonkeyPatch, neo4j_container: tuple[str, str, str]
) -> None:
    """Explicit projects each get their own driver keyed by their own URI.

    Both URIs point at the single testcontainer — this pins the registry
    mapping, not physical isolation.
    """
    uri, _user, _password = neo4j_container
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,beta")
    monkeypatch.setenv("NEO4J_URI_ALPHA", uri)
    monkeypatch.setenv("NEO4J_URI_BETA", uri)

    from chorus.db.registry import close_all_drivers, get_driver

    try:
        assert get_driver("alpha") is not get_driver("beta")
        with get_driver("beta").session() as s:
            assert s.run("RETURN 1 AS one").single()["one"] == 1
    finally:
        close_all_drivers()


def test_close_all_drivers_idempotent(chorus_env: Path) -> None:
    """``close_all_drivers`` is safe to call twice and with no drivers open."""
    from chorus.db.registry import close_all_drivers, get_driver

    close_all_drivers()
    get_driver("default")
    close_all_drivers()
    close_all_drivers()
