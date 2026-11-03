"""FastAPI entrypoint.

Lifespan order matters:
  1. init_logger — so anything that logs during startup is captured
  2. check the principal config — a bad CHORUS_EDGE_SECRET stops startup
     before anything else is touched
  3. open the audit trail — the process's audit chain starts here, with
     one config.audit event stating the operating mode; an unknown
     AUDIT_REQUIRE_PURPOSE action or an unwritable stream stops startup
  4. open Neo4j driver
  5. apply pending migrations — recorded on the chain as the system
  6. register tool registry (imports the package; tools self-register)

Tool routes are wired up in `chorus.api.routers.tools` and import the
registry at import-time, so importing that router triggers tool discovery.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger
from prometheus_fastapi_instrumentator import Instrumentator

from chorus.api.auth.principal import check_principal_config
from chorus.api.correlation import RequestIdMiddleware
from chorus.api.errors import install_error_handlers
from chorus.audit.trail import AuditTrail
from chorus.db.neo4j import close_driver, get_driver
from chorus.ingestion.jobs import JobRegistry
from chorus.migrations.runner import apply_all
from chorus.utils.env_cfg import load_audit_env, load_metrics_env, load_principal_env
from chorus.utils.logger_cfg import init_logger


def _open_audit_trail() -> AuditTrail:
    """Create and start this process's audit chain.

    Returns:
        The started trail; its first events are the chain's ``start`` and
        one ``admin`` / ``config.audit`` event stating the operating mode.

    Raises:
        ValueError: If ``AUDIT_REQUIRE_PURPOSE`` names an unknown action or
            ``AUDIT_OUTPUT`` an unknown mode.
        OSError: If the container stream cannot be opened.
        AuditFailure: If the first events could not be written.
    """
    cfg = load_audit_env()
    principal = load_principal_env()
    trail = AuditTrail.open(cfg.output, required=cfg.require_purpose)
    trail.start(
        {
            "edge_secret_enforced": principal.edge_secret is not None,
            "default_identity_enabled": principal.default_identity is not None and principal.edge_secret is None,
        }
    )
    return trail


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan handler: bring up dependencies, then tear them down.

    Startup order is intentional and load-bearing — see the module
    docstring. State that downstream handlers need (Neo4j driver, audit
    trail) is stashed on ``app.state``. The audit chain is closed last,
    after the job worker and the driver, with its ``shutdown`` event.

    Args:
        app: The FastAPI application; ``app.state`` is populated with
            ``driver``, ``audit`` (the audit trail) and ``jobs``.

    Yields:
        Nothing — control returns to FastAPI for the lifetime of the
        application; the ``finally`` block runs at shutdown.
    """
    init_logger()
    logger.info("chorus starting")
    check_principal_config()
    trail = _open_audit_trail()
    jobs: JobRegistry | None = None
    try:
        driver = get_driver()
        newly = apply_all(driver, audit=trail.system_context())
        if newly:
            logger.info("applied migrations: {}", newly)
        else:
            logger.info("migrations up to date")

        jobs = JobRegistry()
        app.state.driver = driver
        app.state.audit = trail
        app.state.jobs = jobs
        logger.info("chorus ready")
        yield
    finally:
        logger.info("chorus shutting down")
        if jobs is not None:
            jobs.shutdown()
        close_driver()
        trail.close()


app = FastAPI(title="chorus", lifespan=lifespan)
install_error_handlers(app)
app.add_middleware(RequestIdMiddleware)

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
# data (the §76 audit trail is a separate concern, see chorus/audit/trail.py).
# Unauthenticated by design, like /health and /config, so the obs-plane
# scraper can reach it without a principal header.
if load_metrics_env().enabled:
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
