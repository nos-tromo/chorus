"""Loguru sink configuration.

Operational logging only. The §76 BDSG audit logger lives in
`chorus.audit.logger` and writes to SQLite — do not route audit records
through loguru.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from loguru import logger

from chorus.utils.env_cfg import load_path_env


_STDERR_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level:<8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


def init_logger(
    *,
    rotation: str = "5 MB",
    retention: int = 3,
    serialize: bool | None = None,
) -> Path:
    """Install stderr + rotating-file loguru sinks.

    Replaces any sinks loguru was started with. The stderr sink uses a
    colorized human-readable format unless JSON output is requested via
    ``LOG_FORMAT=json``; the file sink always serializes as JSON for
    downstream log shippers.

    Args:
        rotation: Loguru rotation policy for the file sink (size or
            timedelta-style string, e.g. ``"5 MB"`` or ``"1 day"``).
        retention: Number of rotated files to keep.
        serialize: Whether the stderr sink should emit JSON. When
            ``None`` (the default), reads ``LOG_FORMAT`` from the
            environment — ``LOG_FORMAT=json`` enables JSON.

    Returns:
        Absolute path to the active log file.
    """
    log_path = load_path_env().logs
    log_path.parent.mkdir(parents=True, exist_ok=True)

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
    logger.add(
        log_path,
        level="DEBUG",
        rotation=rotation,
        retention=retention,
        enqueue=True,
        serialize=True,
        backtrace=False,
        diagnose=False,
    )
    return log_path
