"""FastAPI entrypoint.

Lifespan order matters:
  1. init_logger — so anything that logs during startup is captured
  2. open one Neo4j driver per configured project (ADR 0017)
  3. apply pending migrations to every project instance
  4. init each project's audit log schema
  5. register tool registry (imports the package; tools self-register)

Tool routes are wired up in `chorus.api.routers.tools` and import the
registry at import-time, so importing that router triggers tool discovery.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger
from prometheus_fastapi_instrumentator import Instrumentator

from chorus.api.errors import install_error_handlers
from chorus.audit.logger import AuditLogger
from chorus.db.registry import close_all_drivers, get_driver
from chorus.ingestion.jobs import JobRegistry
from chorus.migrations.runner import apply_all
from chorus.utils.env_cfg import load_metrics_env, load_project_paths_env, load_projects_env
from chorus.utils.logger_cfg import init_logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan handler: bring up dependencies, then tear them down.

    Startup order is intentional and load-bearing — see the module
    docstring. Per-project state (Neo4j drivers, audit loggers) is
    stashed on ``app.state`` keyed by project name; a bad per-project
    URI fails startup here rather than surfacing on first query.

    Args:
        app: The FastAPI application; ``app.state`` is populated with
            ``projects``, ``drivers``, and ``audits`` for downstream
            handlers (plus transitional single-project ``driver`` /
            ``audit`` aliases until every router is converted).

    Yields:
        Nothing — control returns to FastAPI for the lifetime of the
        application; the ``finally`` block runs at shutdown.
    """
    init_logger()
    logger.info("chorus starting")

    projects = load_projects_env().names
    drivers = {p: get_driver(p) for p in projects}
    for p in projects:
        newly = apply_all(drivers[p])
        if newly:
            logger.info("[{}] applied migrations: {}", p, newly)
        else:
            logger.info("[{}] migrations up to date", p)

    audits: dict[str, AuditLogger] = {}
    for p in projects:
        audits[p] = AuditLogger(load_project_paths_env(p).audit_db)
        audits[p].init_schema()

    app.state.projects = projects
    app.state.drivers = drivers
    app.state.audits = audits
    # Transitional aliases — removed once every router resolves per-project.
    app.state.driver = drivers[projects[0]]
    app.state.audit = audits[projects[0]]
    app.state.jobs = JobRegistry()
    logger.info("chorus ready ({} project(s))", len(projects))
    try:
        yield
    finally:
        logger.info("chorus shutting down")
        app.state.jobs.shutdown()
        close_all_drivers()


app = FastAPI(title="chorus", lifespan=lifespan)
install_error_handlers(app)

# Routers — imported here so the app object owns route registration order.
from chorus.api.routers import agent as _agent_router  # noqa: E402
from chorus.api.routers import config as _config_router  # noqa: E402
from chorus.api.routers import health as _health_router  # noqa: E402
from chorus.api.routers import ingestion as _ingestion_router  # noqa: E402
from chorus.api.routers import stats as _stats_router  # noqa: E402
from chorus.api.routers import tools as _tools_router  # noqa: E402
from chorus.api.routers import whoami as _whoami_router  # noqa: E402

app.include_router(_agent_router.router)
app.include_router(_config_router.router)
app.include_router(_health_router.router)
app.include_router(_ingestion_router.status_router)
app.include_router(_ingestion_router.router)
app.include_router(_stats_router.router)
app.include_router(_tools_router.router)
app.include_router(_whoami_router.router)

# Prometheus metrics — aggregate request counters/latencies only, no user
# data (§76 audit logging is a separate concern, see chorus/audit/logger.py).
# Unauthenticated by design, like /health and /config, so the obs-plane
# scraper can reach it without a principal header.
if load_metrics_env().enabled:
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
