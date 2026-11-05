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
  6. start the scheduled retention sweep, when RETENTION_SWEEP_ENABLED is on
     and RETENTION_SWEEP_INTERVAL_S above zero (it deletes as the system)
  7. register tool registry (imports the package; tools self-register)

Tool routes are wired up in `chorus.api.routers.tools` and import the
registry at import-time, so importing that router triggers tool discovery.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger
from neo4j import Driver
from prometheus_fastapi_instrumentator import Instrumentator

from chorus.api.auth.principal import check_principal_config
from chorus.api.correlation import RequestIdMiddleware
from chorus.api.errors import install_error_handlers
from chorus.audit.trail import AuditTrail
from chorus.db.neo4j import close_driver, get_driver
from chorus.ingestion.jobs import JobRegistry
from chorus.ingestion.raw_store import RawStore
from chorus.migrations.runner import apply_all
from chorus.retention.scheduler import RetentionScheduler
from chorus.retention.sweeper import SweepTotals, sweep
from chorus.utils.env_cfg import (
    RetentionSweepConfig,
    load_audit_env,
    load_metrics_env,
    load_path_env,
    load_principal_env,
    load_retention_env,
    load_retention_sweep_env,
)
from chorus.utils.logger_cfg import init_logger


def _retention_mode(sweep_cfg: RetentionSweepConfig) -> str:
    """Name how this process deletes expired posts.

    Args:
        sweep_cfg: The sweeper configuration.

    Returns:
        ``off`` (nothing is deleted), ``scheduled`` (the backend sweeps every
        interval) or ``on_demand`` (only the one-off sweep command deletes).
    """
    if not sweep_cfg.enabled:
        return "off"
    return "scheduled" if sweep_cfg.interval_s > 0 else "on_demand"


def _open_audit_trail() -> AuditTrail:
    """Create and start this process's audit chain.

    Returns:
        The started trail; its first events are the chain's ``start`` and
        one ``admin`` / ``config.audit`` event stating the operating mode.

    Raises:
        ValueError: If ``AUDIT_REQUIRE_PURPOSE`` names an unknown action,
            ``AUDIT_OUTPUT`` an unknown mode, or the retention sweeper
            configuration is invalid.
        OSError: If the container stream cannot be opened.
        AuditFailure: If the first events could not be written.
    """
    cfg = load_audit_env()
    principal = load_principal_env()
    retention = load_retention_env()
    sweep_cfg = load_retention_sweep_env()
    trail = AuditTrail.open(cfg.output, required=cfg.require_purpose)
    trail.start(
        {
            "edge_secret_enforced": principal.edge_secret is not None,
            "default_identity_enabled": principal.default_identity is not None and principal.edge_secret is None,
            "retention_sweep": _retention_mode(sweep_cfg),
            "retention_period_days": retention.default_days if retention.enabled else None,
        }
    )
    return trail


def _start_retention_sweep(trail: AuditTrail, driver: Driver) -> RetentionScheduler | None:
    """Start the scheduled retention sweep, when it is switched on with an interval.

    Args:
        trail: The process's audit trail; the sweep records as the system.
        driver: The shared Neo4j driver.

    Returns:
        The running scheduler, or ``None`` when the backend does not sweep.
    """
    sweep_cfg = load_retention_sweep_env()
    if _retention_mode(sweep_cfg) != "scheduled":
        return None
    paths = load_path_env()

    def _run(should_stop: Callable[[], bool], totals: SweepTotals) -> SweepTotals:
        return sweep(
            driver,
            RawStore(paths.raw_store),
            paths.chorus_home,
            audit=trail.system_context(),
            retention=load_retention_env(),
            config=sweep_cfg,
            should_stop=should_stop,
            totals=totals,
        )

    scheduler = RetentionScheduler(_run, interval_s=sweep_cfg.interval_s)
    scheduler.start()
    return scheduler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan handler: bring up dependencies, then tear them down.

    Startup order is intentional and load-bearing — see the module
    docstring. State that downstream handlers need (Neo4j driver, audit
    trail) is stashed on ``app.state``. The retention sweep stops first, then
    the job worker and the driver; the audit chain is closed last, with its
    ``shutdown`` event.

    Args:
        app: The FastAPI application; ``app.state`` is populated with
            ``driver``, ``audit`` (the audit trail), ``jobs`` and
            ``retention`` (the sweep's scheduler, or ``None``).

    Yields:
        Nothing — control returns to FastAPI for the lifetime of the
        application; the ``finally`` block runs at shutdown.
    """
    init_logger()
    logger.info("chorus starting")
    check_principal_config()
    trail = _open_audit_trail()
    jobs: JobRegistry | None = None
    retention: RetentionScheduler | None = None
    try:
        driver = get_driver()
        newly = apply_all(driver, audit=trail.system_context())
        if newly:
            logger.info("applied migrations: {}", newly)
        else:
            logger.info("migrations up to date")

        jobs = JobRegistry()
        retention = _start_retention_sweep(trail, driver)
        app.state.driver = driver
        app.state.audit = trail
        app.state.jobs = jobs
        app.state.retention = retention
        logger.info("chorus ready")
        yield
    finally:
        logger.info("chorus shutting down")
        if retention is not None:
            retention.stop()
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
