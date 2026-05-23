"""Health endpoints.

`/health` is a liveness check — it answers 200 if the process is up and the
Neo4j driver is reachable. It does not authenticate; reverse proxies and
orchestrators need to call it without a principal header.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    """Liveness check that verifies Neo4j is reachable.

    Reverse proxies and orchestrators call this without an
    authenticated principal, so no auth dependency is attached. The
    handler issues a trivial ``RETURN 1`` against the configured Neo4j
    database; any driver failure is surfaced as ``503``.

    Args:
        request: The active FastAPI request (used to access the
            shared driver on ``app.state``).

    Returns:
        ``{"status": "ok"}`` when Neo4j is reachable.

    Raises:
        HTTPException: ``503 Service Unavailable`` with the underlying
            driver error when Neo4j cannot be queried.
    """
    driver = request.app.state.driver
    try:
        with driver.session() as s:
            s.run("RETURN 1").consume()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"neo4j unreachable: {exc}",
        ) from exc
    return {"status": "ok"}
