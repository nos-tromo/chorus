"""Loguru sink configuration.

Operational logging only. The §76 BDSG audit logger lives in
`chorus.audit.logger` and writes to SQLite — do not route audit records
through loguru.

A single stderr sink is configured; the container logging driver owns
log retention and rotation (see ``docker/compose.yaml``).
"""

from __future__ import annotations

import os
import sys

from loguru import logger

_STDERR_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level:<8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


def init_logger(*, serialize: bool | None = None) -> None:
    """Install a stderr loguru sink.

    Replaces any sinks loguru was started with. Uses a colorized
    human-readable format unless JSON output is requested via
    ``LOG_FORMAT=json``. ``LOG_LEVEL`` selects the minimum level
    (default ``INFO``).

    Args:
        serialize: Whether to emit JSON. When ``None`` (the default),
            reads ``LOG_FORMAT`` from the environment —
            ``LOG_FORMAT=json`` enables JSON.
    """
    if serialize is None:
        serialize = os.environ.get("LOG_FORMAT", "").lower() == "json"

    level = os.environ.get("LOG_LEVEL", "INFO")

    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format=_STDERR_FORMAT if not serialize else "{message}",
        serialize=serialize,
        backtrace=False,
        diagnose=False,
    )
