"""FastAPI entrypoint.

Lifespan order matters:
  1. init_logger — so anything that logs during startup is captured
  2. open Neo4j driver
  3. apply pending migrations
  4. init audit log schema
  5. register tool registry (imports the package; tools self-register)

Tool routes are wired up in `chorus.api.routers.tools` and import the
registry at import-time, so importing that router triggers tool discovery.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from chorus.audit.logger import AuditLogger
from chorus.db.neo4j import close_driver, get_driver
from chorus.ingestion.jobs import JobRegistry
from chorus.migrations.runner import apply_all
from chorus.utils.env_cfg import load_audit_env
from chorus.utils.logger_cfg import init_logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan handler: bring up dependencies, then tear them down.

    Startup order is intentional and load-bearing — see the module
    docstring. State that downstream handlers need (Neo4j driver,
    audit logger) is stashed on ``app.state``.

    Args:
        app: The FastAPI application; ``app.state`` is populated with
            ``driver`` and ``audit`` for downstream handlers.

    Yields:
        Nothing — control returns to FastAPI for the lifetime of the
        application; the ``finally`` block runs at shutdown.
    """
    init_logger()
    logger.info("chorus starting")

    driver = get_driver()
    newly = apply_all(driver)
    if newly:
        logger.info("applied migrations: {}", newly)
    else:
        logger.info("migrations up to date")

    audit = AuditLogger(load_audit_env().db_path)
    audit.init_schema()

    app.state.driver = driver
    app.state.audit = audit
    app.state.jobs = JobRegistry()
    logger.info("chorus ready")
    try:
        yield
    finally:
        logger.info("chorus shutting down")
        app.state.jobs.shutdown()
        close_driver()


app = FastAPI(title="chorus", lifespan=lifespan)

# Routers — imported here so the app object owns route registration order.
from chorus.api.routers import agent as _agent_router  # noqa: E402
from chorus.api.routers import config as _config_router  # noqa: E402
from chorus.api.routers import health as _health_router  # noqa: E402
from chorus.api.routers import ingestion as _ingestion_router  # noqa: E402
from chorus.api.routers import stats as _stats_router  # noqa: E402
from chorus.api.routers import tools as _tools_router  # noqa: E402

app.include_router(_agent_router.router)
app.include_router(_config_router.router)
app.include_router(_health_router.router)
app.include_router(_ingestion_router.status_router)
app.include_router(_ingestion_router.router)
app.include_router(_stats_router.router)
app.include_router(_tools_router.router)
