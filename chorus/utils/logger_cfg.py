"""Loguru sink configuration.

Operational logging only. The §76 BDSG audit logger lives in
`chorus.audit.logger` and writes to SQLite — do not route audit records
through loguru.

A single stderr sink is configured; the container logging driver owns
log retention and rotation (see ``docker/compose.yaml``). uvicorn's
access log is stdlib logging, outside loguru: setup attaches a filter to
it that drops the query string from every logged request line.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import override

from loguru import logger

_STDERR_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level:<8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

_ACCESS_LOGGER = "uvicorn.access"


class _AccessQueryScrubFilter(logging.Filter):
    """Drop the query string from uvicorn access-log records.

    uvicorn logs each request with ``record.args == (client_addr, method,
    path_with_query, http_version, status)``. A query string can carry
    submitted values, so only the path before ``?`` is kept. No record is
    ever rejected.
    """

    # Identifies the filter across re-imports of this module: a re-import
    # mints a new class, while the stdlib logger and its filters persist.
    chorus_access_scrub = True

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        """Strip the query string from the record's path argument.

        Args:
            record: A ``uvicorn.access`` log record.

        Returns:
            Always ``True``; the record is rewritten in place, not dropped.
        """
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3:
            path = args[2]
            if isinstance(path, str) and "?" in path:
                record.args = (*args[:2], path.split("?", 1)[0], *args[3:])
        return True


def _install_access_log_scrub() -> None:
    """Attach the query-string filter to uvicorn's access logger, once."""
    access = logging.getLogger(_ACCESS_LOGGER)
    if not any(getattr(f, "chorus_access_scrub", False) for f in access.filters):
        access.addFilter(_AccessQueryScrubFilter())


def init_logger(*, serialize: bool | None = None) -> None:
    """Install a stderr loguru sink and scrub uvicorn's access log.

    Replaces any sinks loguru was started with. Uses a colorized
    human-readable format unless JSON output is requested via
    ``LOG_FORMAT=json``. ``LOG_LEVEL`` selects the minimum level
    (default ``INFO``). Runs in the app lifespan, so the access-log
    filter is in place inside the server process before the first
    request is served; repeated calls do not stack filters.

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
    _install_access_log_scrub()
