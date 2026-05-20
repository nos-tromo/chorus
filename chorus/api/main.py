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

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from loguru import logger

from chorus.audit.logger import AuditLogger
from chorus.db.neo4j import close_driver, get_driver
from chorus.migrations.runner import apply_all
from chorus.utils.env_cfg import load_audit_env
from chorus.utils.logger_cfg import init_logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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
    logger.info("chorus ready")
    try:
        yield
    finally:
        logger.info("chorus shutting down")
        close_driver()


app = FastAPI(title="chorus", lifespan=lifespan)

# Routers — imported here so the app object owns route registration order.
from chorus.api.routers import health as _health_router  # noqa: E402
from chorus.api.routers import tools as _tools_router  # noqa: E402

app.include_router(_health_router.router)
app.include_router(_tools_router.router)
