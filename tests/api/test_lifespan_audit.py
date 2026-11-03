"""The backend's audit chain: opened first in the lifespan, closed last.

No Neo4j: the driver and the migration run are stubbed. All values are
invented.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

import pytest

from chorus.audit.trail import AuditTrail
from chorus.utils.audit_event import AuditEmitter
from tests._audit import assert_chain_ok, audit_events

SECRET = "edge-secret-for-tests-0123456789abcdef"


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """Make AuditTrail.open write to a capture buffer instead of stdout.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        The capture buffer.
    """
    stream = io.StringIO()

    def _open(
        cls: type[AuditTrail],
        output: str | None,
        *,
        required: frozenset[str] = frozenset(),
        heartbeat_interval_s: float = 60.0,
    ) -> AuditTrail:
        return cls(AuditEmitter("chorus", "test", stream=stream, heartbeat_interval_s=0), required=required)

    monkeypatch.setattr(AuditTrail, "open", classmethod(_open))
    for key in ("CHORUS_EDGE_SECRET", "CHORUS_DEFAULT_IDENTITY", "AUDIT_REQUIRE_PURPOSE", "AUDIT_OUTPUT"):
        monkeypatch.setenv(key, "")
    return stream


@pytest.mark.parametrize(
    ("secret", "default_identity", "expected"),
    [
        ("", "dev-default", {"edge_secret_enforced": False, "default_identity_enabled": True}),
        (SECRET, "dev-default", {"edge_secret_enforced": True, "default_identity_enabled": False}),
        (SECRET, "", {"edge_secret_enforced": True, "default_identity_enabled": False}),
    ],
    ids=["dev", "secret-overrides-fallback", "production"],
)
def test_open_records_the_operating_mode(
    monkeypatch: pytest.MonkeyPatch,
    captured: io.StringIO,
    secret: str,
    default_identity: str,
    expected: dict[str, bool],
) -> None:
    """The chain's config.audit event states the identity mode and the purpose policy, nothing secret."""
    from chorus.api import main

    monkeypatch.setenv("CHORUS_EDGE_SECRET", secret)
    monkeypatch.setenv("CHORUS_DEFAULT_IDENTITY", default_identity)
    monkeypatch.setenv("AUDIT_REQUIRE_PURPOSE", "query, disclose")
    trail = main._open_audit_trail()
    trail.close()

    config = audit_events(captured)[1]
    assert (config["action"], config["operation"]) == ("admin", "config.audit")
    assert config["criteria"] == {**expected, "require_purpose": ["disclose", "query"]}
    assert SECRET not in captured.getvalue()
    assert "dev-default" not in captured.getvalue()
    assert_chain_ok(captured)


def test_unknown_required_action_stops_startup_before_the_driver(
    monkeypatch: pytest.MonkeyPatch, captured: io.StringIO
) -> None:
    """A misspelt AUDIT_REQUIRE_PURPOSE action never silently switches a requirement off."""
    from chorus.api import main

    def _no_driver() -> None:
        raise AssertionError("Neo4j driver opened before the audit configuration was checked")

    monkeypatch.setattr(main, "init_logger", lambda: None)
    monkeypatch.setattr(main, "get_driver", _no_driver)
    monkeypatch.setenv("AUDIT_REQUIRE_PURPOSE", "quer")

    async def _start() -> None:
        async with main.lifespan(main.app):
            pass

    with pytest.raises(ValueError, match="AUDIT_REQUIRE_PURPOSE"):
        asyncio.run(_start())
    assert captured.getvalue() == ""


def test_lifespan_opens_the_chain_first_and_closes_it_last(
    monkeypatch: pytest.MonkeyPatch, captured: io.StringIO, tmp_path: Path
) -> None:
    """The trail is on app.state while the app serves; shutdown is the chain's last event."""
    from chorus.api import main

    opened: list[str] = []

    def _driver() -> Any:
        opened.append("driver")
        assert audit_events(captured), "the audit chain must be open before the driver"
        return object()

    monkeypatch.setattr(main, "init_logger", lambda: None)
    monkeypatch.setattr(main, "get_driver", _driver)
    monkeypatch.setattr(main, "close_driver", lambda: opened.append("closed"))
    monkeypatch.setattr(main, "apply_all", lambda *_a, **_kw: [])
    monkeypatch.setenv("CHORUS_HOME", str(tmp_path))

    async def _serve() -> None:
        async with main.lifespan(main.app):
            assert isinstance(main.app.state.audit, AuditTrail)
            assert audit_events(captured)[-1]["operation"] == "config.audit"

    asyncio.run(_serve())
    assert opened == ["driver", "closed"]
    events = audit_events(captured)
    assert [e["operation"] for e in events] == ["start", "config.audit", "shutdown"]
    assert_chain_ok(captured)
