"""Multi-instance /health tests (ADR 0017)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeSession:
    """Session stub whose ``run`` optionally raises."""

    def __init__(self, fail: bool) -> None:
        self._fail = fail

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def run(self, _query: str) -> Any:
        if self._fail:
            raise RuntimeError("boom")

        class _Result:
            def consume(self) -> None:
                return None

        return _Result()


class _FakeDriver:
    """Driver stub yielding sessions that succeed or fail."""

    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    def session(self) -> _FakeSession:
        return _FakeSession(self._fail)


def _build_app(drivers: dict[str, Any]) -> FastAPI:
    from chorus.api.routers import health as health_router

    app = FastAPI()
    app.include_router(health_router.router)
    app.state.drivers = drivers
    return app


def test_health_all_projects_ok() -> None:
    """Every instance reachable → 200 with per-project statuses."""
    client = TestClient(_build_app({"alpha": _FakeDriver(), "beta": _FakeDriver()}))

    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "projects": {"alpha": "ok", "beta": "ok"}}


def test_health_degraded_reports_failing_project_503() -> None:
    """One dead instance → 503, and the body names which project is down."""
    client = TestClient(_build_app({"alpha": _FakeDriver(), "beta": _FakeDriver(fail=True)}))

    r = client.get("/health")
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "projects": {"alpha": "ok", "beta": "unreachable"}}
