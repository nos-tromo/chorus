"""Public client-bootstrap config.

Exposes the handful of deployment facts the SPA needs before it can make any
authenticated call: the active UI language, whether the ingestion UI is on,
the release version, and which audit actions need a stated purpose — so the
purpose bar can warn before the backend refuses a request without one.
Unauthenticated and unaudited by design (like /health) — none of it is
sensitive. Language and ingestion flag are read from env at request time so
tests and hot-reloads see current values; the purpose policy is read from
the process's audit trail, which is what enforces it.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from chorus import __version__
from chorus.audit.trail import AuditTrail
from chorus.utils.env_cfg import load_ingestion_ui_env, load_language_env

router = APIRouter(tags=["config"])


class ConfigOut(BaseModel):
    """Client bootstrap config."""

    language: str
    ingestion_enabled: bool
    version: str
    require_purpose: list[str]


@router.get("/config", response_model=ConfigOut)
def get_config(request: Request) -> ConfigOut:
    """Return the SPA's bootstrap config (language, ingestion flag, version, purpose policy).

    Args:
        request: The active request; its app's audit trail holds the
            purpose policy (``AUDIT_REQUIRE_PURPOSE``, parsed at startup).

    Returns:
        The bootstrap config. ``require_purpose`` lists, sorted, the actions
        the backend refuses without a stated purpose; empty when none.
    """
    trail: AuditTrail = request.app.state.audit
    return ConfigOut(
        language=load_language_env().code,
        ingestion_enabled=load_ingestion_ui_env().enabled,
        version=__version__,
        require_purpose=sorted(trail.required),
    )


class VersionOut(BaseModel):
    """App release version."""

    version: str


@router.get("/version", response_model=VersionOut)
def get_version() -> VersionOut:
    """Return the running app version (unauthenticated)."""
    return VersionOut(version=__version__)
