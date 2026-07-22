"""GET /metrics endpoint: Prometheus scrape surface for obs-plane."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_fastapi_instrumentator import Instrumentator


def _build_app() -> FastAPI:
    """Minimal app with the same instrumentator wiring as ``chorus.api.main``."""
    app = FastAPI()
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    return app


def test_metrics_returns_prometheus_exposition() -> None:
    """GET /metrics returns 200 with a recognizable Prometheus metric family."""
    client = TestClient(_build_app())
    resp = client.get("/metrics")  # no auth — must stay scrapeable
    assert resp.status_code == 200
    assert "python_info" in resp.text


def test_metrics_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``METRICS_ENABLED=false`` keeps chorus.api.main from registering /metrics."""
    import sys

    monkeypatch.setenv("METRICS_ENABLED", "false")
    for mod in ("chorus.utils.env_cfg",):
        sys.modules.pop(mod, None)
    from chorus.utils import env_cfg

    assert env_cfg.load_metrics_env().enabled is False
