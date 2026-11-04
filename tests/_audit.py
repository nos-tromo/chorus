"""Helpers for asserting on captured audit events.

Tests capture a process's audit chain in an ``io.StringIO`` (the
``audit_stream`` fixture) and read it back as parsed events.
"""

from __future__ import annotations

import io
import json
from typing import Any

from chorus.audit.trail import AuditTrail
from chorus.utils.audit_event import AuditEmitter, verify_chain

#: Event keys that must never appear: there is no field for content.
CONTENT_KEYS = frozenset({"text", "content", "question", "answer", "messages_text", "prompt"})


def audit_events(stream: io.StringIO) -> list[dict[str, Any]]:
    """Parse every audit line written to ``stream`` so far.

    Args:
        stream: The captured audit stream.

    Returns:
        The events in write order.
    """
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def operation_events(stream: io.StringIO, *, lifecycle: bool = False) -> list[dict[str, Any]]:
    """Return the captured events that record operations.

    Args:
        stream: The captured audit stream.
        lifecycle: Whether to keep the chain's own lifecycle events and the
            ``config.audit`` event.

    Returns:
        The events in write order, without the chain bookkeeping unless
        ``lifecycle`` is set.
    """
    events = audit_events(stream)
    if lifecycle:
        return events
    return [e for e in events if e["action"] != "lifecycle" and e["operation"] != "config.audit"]


def assert_chain_ok(stream: io.StringIO) -> None:
    """Assert that the captured stream is one intact chain (or several).

    Args:
        stream: The captured audit stream.
    """
    report = verify_chain(audit_events(stream))
    assert report.ok, report.problems


def started_trail(stream: io.StringIO, *, required: frozenset[str] = frozenset()) -> AuditTrail:
    """Return a started audit trail writing to ``stream``, with no heartbeat.

    For tests that need their own purpose policy or a stream they break on
    purpose; the shared ``audit_trail`` fixture covers the rest.

    Args:
        stream: The capture buffer.
        required: The actions that need a stated purpose.

    Returns:
        The started :class:`~chorus.audit.trail.AuditTrail`.
    """
    trail = AuditTrail(AuditEmitter("chorus", "test", stream=stream, heartbeat_interval_s=0), required=required)
    trail.start({"edge_secret_enforced": False, "default_identity_enabled": True})
    return trail
