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
    driver = request.app.state.driver
    try:
        with driver.session() as s:
            s.run("RETURN 1").consume()
    except Exception as exc:  # noqa: BLE001 — surface the failure as 503
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"neo4j unreachable: {exc}",
        )
    return {"status": "ok"}
