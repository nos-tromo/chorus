"""Per-project Neo4j driver registry (ADR 0017).

One driver per configured project, created lazily and pooled for the
process lifetime. Project names are validated against the configured set
before a driver is built, so a typo (or an unvetted value reaching this
layer) fails loudly instead of lazily connecting somewhere unintended.
"""

from __future__ import annotations

from neo4j import Driver, GraphDatabase

from chorus.utils.env_cfg import load_neo4j_env, load_projects_env

_drivers: dict[str, Driver] = {}

# Bound connection establishment so a down instance degrades /health and
# per-request errors quickly instead of hanging on bolt retries.
_CONNECTION_TIMEOUT_S = 5.0


class UnknownProjectError(ValueError):
    """Raised when a project name is not in the configured project set."""


def get_driver(project: str) -> Driver:
    """Return the pooled driver for one project's Neo4j instance.

    Args:
        project: A project name from :func:`load_projects_env`.

    Returns:
        The cached :class:`neo4j.Driver` for that project, constructed on
        first use from :func:`load_neo4j_env`.

    Raises:
        UnknownProjectError: If ``project`` is not configured.
    """
    if project not in load_projects_env().names:
        raise UnknownProjectError(f"Unknown project: {project!r}")
    if project not in _drivers:
        cfg = load_neo4j_env(project)
        _drivers[project] = GraphDatabase.driver(
            cfg.uri,
            auth=(cfg.user, cfg.password),
            connection_timeout=_CONNECTION_TIMEOUT_S,
        )
    return _drivers[project]


def close_all_drivers() -> None:
    """Close every cached driver and clear the registry.

    Safe to call multiple times and when no driver was ever constructed.
    The FastAPI lifespan handler and the CLIs invoke this on shutdown.
    """
    for drv in _drivers.values():
        drv.close()
    _drivers.clear()
