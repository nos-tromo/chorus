"""Neo4j driver factory.

A single process-wide driver is created lazily and reused. Callers should
not construct their own — use `get_driver()` and `session()`.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from neo4j import Driver, GraphDatabase, Session

from chorus.utils.env_cfg import load_neo4j_env


_driver: Driver | None = None


def get_driver() -> Driver:
    """Return the process-wide Neo4j driver, constructing it on first call.

    The driver is cached in module state so connections are pooled across
    the whole app lifetime. The FastAPI lifespan handler is responsible
    for calling :func:`close_driver` on shutdown.

    Returns:
        The shared :class:`neo4j.Driver` instance.
    """
    global _driver
    if _driver is None:
        cfg = load_neo4j_env()
        _driver = GraphDatabase.driver(cfg.uri, auth=(cfg.user, cfg.password))
    return _driver


def close_driver() -> None:
    """Close the process-wide Neo4j driver if one was created.

    Safe to call multiple times and safe to call when no driver was ever
    constructed. The FastAPI lifespan handler invokes this on shutdown.
    """
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


@contextmanager
def session(database: str | None = None) -> Iterator[Session]:
    """Yield a Neo4j session bound to the configured (or named) database.

    Wraps :meth:`neo4j.Driver.session` so callers can use a single
    ``with`` statement without threading the driver through their code.
    The session is closed automatically on exit, whether or not the
    block raises.

    Args:
        database: Optional logical database name. Defaults to the value
            of ``NEO4J_DATABASE`` from the environment.

    Yields:
        An open :class:`neo4j.Session`.
    """
    cfg = load_neo4j_env()
    drv = get_driver()
    with drv.session(database=database or cfg.database) as s:
        yield s
