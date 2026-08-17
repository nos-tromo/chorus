"""Health endpoints.

`/health` is a liveness check — it answers 200 if the process is up and
every project's Neo4j instance is reachable (ADR 0017). It does not
authenticate; reverse proxies and orchestrators need to call it without a
principal header. Project names are server configuration, not user data,
so listing them here is acceptable behind the gateway.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from loguru import logger

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request) -> JSONResponse:
    """Liveness check that verifies every project's Neo4j is reachable.

    Reverse proxies and orchestrators call this without an
    authenticated principal, so no auth dependency is attached. The
    handler issues a trivial ``RETURN 1`` against each configured
    instance; any failure degrades the overall status to ``503`` while
    the body still reports per-project results so operators can see
    which instance is down.

    Args:
        request: The active FastAPI request (used to access the
            per-project drivers on ``app.state``).

    Returns:
        ``{"status": "ok", "projects": {...}}`` with ``200`` when every
        instance answers; ``{"status": "degraded", ...}`` with ``503``
        when at least one does not.
    """
    results: dict[str, str] = {}
    for project, driver in request.app.state.drivers.items():
        try:
            with driver.session() as s:
                s.run("RETURN 1").consume()
            results[project] = "ok"
        except Exception as exc:
            logger.error(f"[{project}] neo4j unreachable: {exc}")
            results[project] = "unreachable"
    healthy = all(v == "ok" for v in results.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ok" if healthy else "degraded", "projects": results},
    )
