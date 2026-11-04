"""The audit trail glue: chain start, id chunking, purpose policy, fail-closed writes.

Every event here is written to an ``io.StringIO`` and every chain is checked
with ``verify_chain()``. All values are invented.
"""

from __future__ import annotations

import atexit
import io

import pytest

from chorus.audit.trail import AuditContext, AuditFailure, AuditTrail, PurposeRequired, subject_refs
from chorus.utils.audit_event import Actor, Purpose, Target
from tests._audit import assert_chain_ok, audit_events, operation_events, started_trail

ALICE = Actor(user="alice-example", via="edge")


def test_start_writes_the_chain_start_then_the_operating_mode() -> None:
    """A chain opens with its start event and one config.audit event stating the mode."""
    stream = io.StringIO()
    trail = started_trail(stream, required=frozenset({"query", "disclose"}))
    trail.close()

    events = audit_events(stream)
    assert [(e["action"], e["operation"]) for e in events] == [
        ("lifecycle", "start"),
        ("admin", "config.audit"),
        ("lifecycle", "shutdown"),
    ]
    config = events[1]
    assert config["actor"] == {"user": None, "via": "system"}
    assert config["criteria"] == {
        "edge_secret_enforced": False,
        "default_identity_enabled": True,
        "require_purpose": ["disclose", "query"],
    }
    assert config["service"] == "chorus"
    assert_chain_ok(stream)


def test_context_events_carry_actor_purpose_and_request_id(audit_trail: AuditTrail, audit_stream: io.StringIO) -> None:
    """Every event written through a context names its caller, purpose and request id."""
    ctx = audit_trail.context(ALICE, purpose=Purpose(case_ref="case-0001", reason="review"), request_id="req-1")
    event_id = ctx.emit("query", "tool.example", target=Target(kind="mixed", ids=["author:a-1"], count=1))

    [event] = operation_events(audit_stream)
    assert event["event_id"] == event_id
    assert ctx.event_ids == [event_id]
    assert event["actor"] == {"user": "alice-example", "via": "edge"}
    assert event["purpose"] == {"case_ref": "case-0001", "reason": "review"}
    assert event["request_id"] == "req-1"


def test_emit_refs_splits_ids_into_continuation_events(audit_trail: AuditTrail, audit_stream: io.StringIO) -> None:
    """Past 200 ids, the first chunk stays on the event and each further chunk gets a continuation."""
    ctx = audit_trail.context(ALICE, request_id="req-2")
    ids = [f"author:a-{i:04d}" for i in range(450)]
    primary = ctx.emit_refs("query", "tool.example", kind="mixed", ids=ids, count=450, criteria={"k": 1})

    events = operation_events(audit_stream)
    assert [e["operation"] for e in events] == ["tool.example", "tool.example.ids", "tool.example.ids"]
    first, second, third = events
    assert first["event_id"] == primary
    assert first["target"] == {"kind": "mixed", "ids": ids[:200], "count": 450}
    assert second["target"] == {"kind": "mixed", "ids": ids[200:400], "count": 200}
    assert third["target"] == {"kind": "mixed", "ids": ids[400:], "count": 50}
    for continuation in (second, third):
        assert continuation["parent_event_id"] == primary
        assert continuation["action"] == "query"
        assert continuation["request_id"] == "req-2"
        assert "criteria" not in continuation
    assert all("ids_truncated" not in e["target"] for e in events)
    assert ctx.event_ids == [primary]


def test_emit_refs_without_ids_still_sets_the_count(audit_trail: AuditTrail, audit_stream: io.StringIO) -> None:
    """An empty result is one event with a zero count and no ids."""
    audit_trail.context(ALICE).emit_refs("query", "tool.example", kind="mixed", ids=[], count=0)
    [event] = operation_events(audit_stream)
    assert event["target"] == {"kind": "mixed", "count": 0}


def test_subject_refs_order_and_dedupe() -> None:
    """Authors come first, then entities, then posts; each kind deduplicated, blanks dropped."""
    refs = subject_refs(posts=["p-1", "p-1"], entities=["e-1", ""], authors=["a-2", "a-1", "a-2"])
    assert refs == ["author:a-2", "author:a-1", "entity:e-1", "post:p-1"]


def test_purpose_not_required_by_default(audit_trail: AuditTrail, audit_stream: io.StringIO) -> None:
    """With AUDIT_REQUIRE_PURPOSE empty, nothing is refused and nothing is written."""
    audit_trail.context(ALICE).require_purpose(("query", "ai_call"), "agent.answer", {"k": 1})
    assert operation_events(audit_stream) == []


def test_missing_purpose_is_refused_with_one_denied_event() -> None:
    """A required action without a stated purpose writes one denied event and raises."""
    stream = io.StringIO()
    trail = started_trail(stream, required=frozenset({"ai_call"}))
    ctx = trail.context(ALICE, request_id="req-3")
    with pytest.raises(PurposeRequired) as exc:
        ctx.require_purpose(("query", "ai_call"), "agent.answer", {"messages": 1})
    trail.close()

    assert (exc.value.action, exc.value.operation) == ("ai_call", "agent.answer")
    [event] = operation_events(stream)
    assert (event["action"], event["operation"], event["outcome"]) == ("ai_call", "agent.answer", "denied")
    assert event["criteria"] == {"messages": 1}
    assert event["request_id"] == "req-3"
    assert "target" not in event
    assert_chain_ok(stream)


@pytest.mark.parametrize(
    "purpose",
    [Purpose(case_ref="case-0001"), Purpose(reason="review"), Purpose(case_ref="case-0001", reason="review")],
)
def test_stated_purpose_passes_the_requirement(purpose: Purpose) -> None:
    """Either purpose field alone states a purpose."""
    stream = io.StringIO()
    trail = started_trail(stream, required=frozenset({"query"}))
    trail.context(ALICE, purpose=purpose).require_purpose(("query",), "tool.example")
    trail.close()
    assert operation_events(stream) == []
    assert_chain_ok(stream)


def test_blank_purpose_counts_as_none() -> None:
    """A Purpose with both fields unset states nothing."""
    ctx = AuditContext(trail=AuditTrail.__new__(AuditTrail), actor=ALICE, purpose=Purpose())
    assert ctx.purpose_stated is False


def test_write_failure_raises_audit_failure() -> None:
    """A stream that cannot be written fails the operation instead of losing the event."""
    stream = io.StringIO()
    trail = started_trail(stream)
    stream.close()
    with pytest.raises(AuditFailure):
        trail.context(ALICE).emit("query", "tool.example")
    atexit.unregister(trail.emitter.close)  # the broken chain cannot write its shutdown event


def test_closed_trail_raises_audit_failure() -> None:
    """Writing through a closed chain fails closed too."""
    stream = io.StringIO()
    trail = started_trail(stream)
    trail.close()
    with pytest.raises(AuditFailure):
        trail.context(ALICE).emit("query", "tool.example")
    assert_chain_ok(stream)


def test_open_rejects_an_unknown_output_mode() -> None:
    """AUDIT_OUTPUT must name stdout or container."""
    with pytest.raises(ValueError, match="AUDIT_OUTPUT"):
        AuditTrail.open("syslog")


def test_open_on_stdout_writes_to_the_process_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Unset AUDIT_OUTPUT means the process's own stdout, one JSON line per event."""
    trail = AuditTrail.open(None, heartbeat_interval_s=0)
    trail.start({})
    trail.close()
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3
    assert all('"stream":"audit"' in line for line in lines)
