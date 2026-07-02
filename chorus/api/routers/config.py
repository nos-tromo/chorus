"""Public client-bootstrap config.

Exposes the handful of deployment facts the SPA needs before it can make any
authenticated call: the active UI language and whether the ingestion UI is on.
Unauthenticated by design (like /health) — it returns only a language code and
two booleans, no sensitive data — and reads env at request time so tests and
hot-reloads see current values.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from chorus import __version__
from chorus.utils.env_cfg import load_ingestion_ui_env, load_language_env

router = APIRouter(tags=["config"])


class ConfigOut(BaseModel):
    """Client bootstrap config."""

    language: str
    ingestion_enabled: bool
    version: str


@router.get("/config", response_model=ConfigOut)
def get_config() -> ConfigOut:
    """Return the SPA's bootstrap config (language, ingestion flag, version)."""
    return ConfigOut(
        language=load_language_env().code,
        ingestion_enabled=load_ingestion_ui_env().enabled,
        version=__version__,
    )


class VersionOut(BaseModel):
    """App release version."""

    version: str


@router.get("/version", response_model=VersionOut)
def get_version() -> VersionOut:
    """Return the running app version (unauthenticated)."""
    return VersionOut(version=__version__)
