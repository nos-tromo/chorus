"""uvicorn's access log is installed without query strings.

``init_logger`` (run by the app lifespan inside the server process) attaches a
filter to the stdlib ``uvicorn.access`` logger that keeps only the path before
``?``. The records below are shaped exactly like uvicorn's own access-log call.
"""

from __future__ import annotations

import importlib
import io
import logging
import sys
from collections.abc import Iterator

import pytest
from loguru import logger

_PLACEHOLDER = "placeholder-handle-0001"
_ACCESS_FORMAT = '%s - "%s %s HTTP/%s" %d'


@pytest.fixture
def access_logger() -> Iterator[logging.Logger]:
    """Uvicorn's access logger, restored to its prior filters and handlers."""
    access = logging.getLogger("uvicorn.access")
    filters, handlers = list(access.filters), list(access.handlers)
    level, propagate = access.level, access.propagate
    try:
        yield access
    finally:
        for f in [f for f in access.filters if f not in filters]:
            access.removeFilter(f)
        for h in [h for h in access.handlers if h not in handlers]:
            access.removeHandler(h)
        access.setLevel(level)
        access.propagate = propagate


@pytest.fixture
def restore_loguru() -> Iterator[None]:
    """Put loguru back on its default stderr sink after ``init_logger`` swapped it."""
    try:
        yield
    finally:
        logger.remove()
        logger.add(sys.stderr)


def _access_record(path: str) -> logging.LogRecord:
    """Build an access-log record as uvicorn emits it."""
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        _ACCESS_FORMAT,
        ("10.0.0.2:51000", "GET", path, "1.1", 200),
        None,
    )


def _scrub_filters(access: logging.Logger) -> list[logging.Filter]:
    """Return the query-string filters attached to ``access``."""
    return [f for f in access.filters if isinstance(f, logging.Filter) and getattr(f, "chorus_access_scrub", False)]


def test_filter_drops_query_string() -> None:
    """The path keeps everything before ``?``; the record is never rejected."""
    from chorus.utils.logger_cfg import _AccessQueryScrubFilter

    record = _access_record(f"/stats?author={_PLACEHOLDER}&limit=5")
    assert _AccessQueryScrubFilter().filter(record) is True
    assert record.getMessage() == '10.0.0.2:51000 - "GET /stats HTTP/1.1" 200'
    assert _PLACEHOLDER not in record.getMessage()


def test_filter_leaves_other_records_untouched() -> None:
    """Paths without a query, argument-less records and short tuples pass as-is."""
    from chorus.utils.logger_cfg import _AccessQueryScrubFilter

    scrub = _AccessQueryScrubFilter()

    plain = _access_record("/health")
    assert scrub.filter(plain) is True
    assert plain.getMessage() == '10.0.0.2:51000 - "GET /health HTTP/1.1" 200'

    bare = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "no arguments", None, None)
    assert scrub.filter(bare) is True
    assert bare.getMessage() == "no arguments"

    short = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "%s", ("a?b",), None)
    assert scrub.filter(short) is True
    assert short.args == ("a?b",)


@pytest.mark.usefixtures("restore_loguru")
def test_init_logger_installs_one_filter(access_logger: logging.Logger) -> None:
    """Repeated setup, even across a re-import of the module, keeps one filter."""
    from chorus.utils import logger_cfg

    for f in _scrub_filters(access_logger):
        access_logger.removeFilter(f)

    logger_cfg.init_logger()
    logger_cfg.init_logger()
    assert len(_scrub_filters(access_logger)) == 1

    importlib.reload(logger_cfg).init_logger()
    assert len(_scrub_filters(access_logger)) == 1


@pytest.mark.usefixtures("restore_loguru")
def test_uvicorn_formatter_renders_scrubbed_request_line(access_logger: logging.Logger) -> None:
    """End to end through uvicorn's own AccessFormatter: no query string in the output."""
    from uvicorn.logging import AccessFormatter

    from chorus.utils.logger_cfg import init_logger

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False))
    access_logger.addHandler(handler)
    access_logger.setLevel(logging.INFO)
    access_logger.propagate = False

    init_logger()
    access_logger.info(_ACCESS_FORMAT, "10.0.0.2:51000", "GET", f"/tools?q={_PLACEHOLDER}", "1.1", 200)

    out = stream.getvalue()
    assert '"GET /tools HTTP/1.1" 200' in out
    assert _PLACEHOLDER not in out


@pytest.mark.usefixtures("restore_loguru")
def test_init_logger_moves_stdout_access_log_to_stderr(access_logger: logging.Logger) -> None:
    """Uvicorn's default access handler writes to stdout; setup points it at stderr.

    stdout carries the audit stream alone, so no ops log line may land there.
    A handler writing elsewhere is left alone.
    """
    from chorus.utils.logger_cfg import init_logger

    to_stdout = logging.StreamHandler(sys.stdout)
    elsewhere = io.StringIO()
    to_other = logging.StreamHandler(elsewhere)
    access_logger.addHandler(to_stdout)
    access_logger.addHandler(to_other)

    init_logger()

    assert to_stdout.stream is sys.stderr
    assert to_other.stream is elsewhere
