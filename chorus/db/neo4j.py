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
    global _driver
    if _driver is None:
        cfg = load_neo4j_env()
        _driver = GraphDatabase.driver(cfg.uri, auth=(cfg.user, cfg.password))
    return _driver


def close_driver() -> None:
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


@contextmanager
def session(database: str | None = None) -> Iterator[Session]:
    cfg = load_neo4j_env()
    drv = get_driver()
    with drv.session(database=database or cfg.database) as s:
        yield s
