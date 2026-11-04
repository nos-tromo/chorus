"""Chorus's side of the federation's data-protection audit trail (§76 BDSG).

Each process that processes personal data writes one hash-chained stream of
audit events through :mod:`chorus.utils.audit_event`: the backend from its
lifespan, every CLI command on its own. :class:`AuditTrail` owns that
process's emitter and the purpose policy (``AUDIT_REQUIRE_PURPOSE``).
:class:`AuditContext` binds the trail to one caller — actor, stated purpose,
correlation id — and is what audited code receives.

Events reference what an operation touched by id and count, never by
content. Writes fail closed: a write that fails raises :class:`AuditFailure`,
and the operation it records fails with it. Nothing may catch it to carry
on, and nothing falls back to the operational log.

This module reads no environment; the caller passes the configuration in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, TextIO

from chorus import __version__
from chorus.utils.audit_event import ACTIONS, Actor, AiCall, AuditEmitter, AuditWriteError, Purpose, Target
from chorus.utils.audit_runtime import id_chunks, open_audit_stream

#: The compose project name, which the audit store checks every event against.
SERVICE: Final = "chorus"
#: The actions ``AUDIT_REQUIRE_PURPOSE`` may name.
PURPOSE_ACTIONS: Final = ACTIONS - {"lifecycle"}

_SYSTEM: Final = Actor(user=None, via="system")


class AuditFailure(RuntimeError):
    """An audit event could not be written; the operation it records fails with it."""


class PurposeRequired(Exception):
    """An operation needs a stated purpose and the caller stated none.

    The refusal has already been recorded as a ``denied`` event when this is
    raised. The API answers it with 428, a CLI command with exit code 2.

    Attributes:
        action: The action that requires a stated purpose.
        operation: The refused operation.
    """

    def __init__(self, action: str, operation: str) -> None:
        """Record which action and operation were refused.

        Args:
            action: The action that requires a stated purpose.
            operation: The refused operation.
        """
        super().__init__(f"{operation} ({action}) needs a stated purpose")
        self.action = action
        self.operation = operation


def subject_refs(
    *,
    authors: Iterable[str] = (),
    entities: Iterable[str] = (),
    posts: Iterable[str] = (),
) -> list[str]:
    """Return kind-prefixed references for a ``mixed`` target, data subjects first.

    Args:
        authors: ``:Author.id`` values.
        entities: ``:Entity.id`` values.
        posts: ``:Post.uuid`` values.

    Returns:
        ``author:<id>`` references, then ``entity:<id>``, then ``post:<uuid>``;
        each kind deduplicated in first-seen order, blank ids dropped.
    """
    refs: list[str] = []
    for prefix, values in (("author", authors), ("entity", entities), ("post", posts)):
        seen: set[str] = set()
        for value in values:
            if value and value not in seen:
                seen.add(value)
                refs.append(f"{prefix}:{value}")
    return refs


class AuditTrail:
    """One process's audit chain plus the purpose policy it enforces.

    Attributes:
        emitter: The process's emitter; one chain per trail.
        required: The actions that need a stated purpose.
    """

    def __init__(
        self,
        emitter: AuditEmitter,
        *,
        required: frozenset[str] = frozenset(),
        stream: TextIO | None = None,
    ) -> None:
        """Wrap an emitter; nothing is written until :meth:`start`.

        Args:
            emitter: The emitter writing this process's chain.
            required: The actions that need a stated purpose, as
                ``audit_runtime.parse_required_actions`` returns them.
            stream: The stream the emitter writes to, when the trail owns it
                and must close it after the chain; ``None`` for stdout.
        """
        self.emitter = emitter
        self.required = required
        self._stream = stream

    @classmethod
    def open(
        cls,
        output: str | None,
        *,
        required: frozenset[str] = frozenset(),
        heartbeat_interval_s: float = 60.0,
    ) -> AuditTrail:
        """Create the trail for this process on the stream ``AUDIT_OUTPUT`` selects.

        Args:
            output: ``AUDIT_OUTPUT``: unset or ``stdout`` for the process's
                stdout, ``container`` for the container's main stdout.
            required: The actions that need a stated purpose.
            heartbeat_interval_s: Seconds between heartbeat events.

        Returns:
            A trail that has not been started yet.

        Raises:
            ValueError: If ``output`` names an unknown mode.
            OSError: If the container stream cannot be opened.
        """
        stream = open_audit_stream(output)
        emitter = AuditEmitter(SERVICE, __version__, stream=stream, heartbeat_interval_s=heartbeat_interval_s)
        return cls(emitter, required=required, stream=stream)

    def start(self, mode: Mapping[str, object]) -> None:
        """Open the chain and record the operating mode as its first event.

        Writes the chain's ``start`` event, then one ``admin`` /
        ``config.audit`` event whose criteria are ``mode`` plus the sorted
        actions that need a stated purpose.

        Args:
            mode: Operating-mode facts to record. Never secrets or paths.

        Raises:
            AuditFailure: If either event could not be written.
        """
        try:
            self.emitter.start()
            self.emitter.emit(
                "admin",
                "config.audit",
                _SYSTEM,
                criteria={**mode, "require_purpose": sorted(self.required)},
            )
        except (AuditWriteError, RuntimeError) as exc:
            raise AuditFailure("the audit chain could not be started") from exc

    def close(self) -> None:
        """Close the chain with its ``shutdown`` event, then release the stream.

        Raises:
            AuditFailure: If the ``shutdown`` event could not be written.
        """
        try:
            self.emitter.close()
        except (AuditWriteError, RuntimeError) as exc:
            raise AuditFailure("the audit chain could not be closed") from exc
        finally:
            if self._stream is not None and not self._stream.closed:
                self._stream.close()

    def context(
        self,
        actor: Actor,
        *,
        purpose: Purpose | None = None,
        request_id: str | None = None,
    ) -> AuditContext:
        """Bind this trail to one caller.

        Args:
            actor: Who causes the events.
            purpose: The caller's stated purpose, if any.
            request_id: The gateway's correlation id, if any.

        Returns:
            A fresh :class:`AuditContext`.
        """
        return AuditContext(trail=self, actor=actor, purpose=purpose, request_id=request_id)

    def system_context(self) -> AuditContext:
        """Bind this trail to the system itself (startup work, no caller).

        Returns:
            An :class:`AuditContext` with ``Actor(None, "system")`` and no
            purpose.
        """
        return self.context(_SYSTEM)


@dataclass
class AuditContext:
    """The audit trail bound to one caller: what audited code receives.

    Every event written through a context carries its actor, purpose and
    request id.

    Attributes:
        trail: The process's trail.
        actor: Who causes the events.
        purpose: The caller's stated purpose, if any.
        request_id: The gateway's correlation id, if any.
        event_ids: Ids of the operation events written through this context,
            in order (continuation events excluded) — e.g. the tool calls of
            one agent turn.
    """

    trail: AuditTrail
    actor: Actor
    purpose: Purpose | None = None
    request_id: str | None = None
    event_ids: list[str] = field(default_factory=list)

    @property
    def purpose_stated(self) -> bool:
        """Whether the caller stated a purpose (at least one non-empty field)."""
        return self.purpose is not None and bool(self.purpose.case_ref or self.purpose.reason)

    def scoped(self) -> AuditContext:
        """Return a context for the same caller that records its own event ids.

        Returns:
            A new :class:`AuditContext` sharing actor, purpose and request id,
            with an empty :attr:`event_ids`.
        """
        return AuditContext(trail=self.trail, actor=self.actor, purpose=self.purpose, request_id=self.request_id)

    def require_purpose(
        self,
        actions: Iterable[str],
        operation: str,
        criteria: Mapping[str, object] | None = None,
    ) -> None:
        """Refuse an operation whose actions need a purpose the caller did not state.

        Call it before the operation processes anything. The refusal is
        recorded as one ``denied`` event for the first required action, with
        the operation's criteria and no target.

        Args:
            actions: The actions the operation performs, most significant
                first.
            operation: The operation about to run.
            criteria: The operation's parameters, for the ``denied`` event.

        Raises:
            PurposeRequired: If one of ``actions`` needs a stated purpose and
                none was stated.
            AuditFailure: If the ``denied`` event could not be written.
        """
        if self.purpose_stated:
            return
        for action in actions:
            if action in self.trail.required:
                self.emit(action, operation, outcome="denied", criteria=criteria)
                raise PurposeRequired(action, operation)

    def deny(self, action: str, operation: str, criteria: Mapping[str, object] | None = None) -> str:
        """Record that an authenticated caller was refused an operation.

        Args:
            action: The action of the refused operation.
            operation: The refused operation.
            criteria: The operation's parameters, if known.

        Returns:
            The ``denied`` event's id.

        Raises:
            AuditFailure: If the event could not be written.
        """
        return self.emit(action, operation, outcome="denied", criteria=criteria)

    def emit(
        self,
        action: str,
        operation: str,
        *,
        outcome: str = "ok",
        target: Target | None = None,
        criteria: Mapping[str, object] | None = None,
        recipient: str | None = None,
        ai: AiCall | None = None,
        parent_event_id: str | None = None,
    ) -> str:
        """Write one operation event and remember its id.

        Args:
            action: One of the audit library's actions.
            operation: What exactly happened, dotted and lowercase.
            outcome: ``ok``, ``denied`` or ``error``.
            target: What the operation touched, by reference.
            criteria: The caller's parameters (and, for writes, counts).
            recipient: Where data went, e.g. ``inference:chat``.
            ai: Model provenance, for ``ai_call`` events.
            parent_event_id: An earlier event of the same operation.

        Returns:
            The event's id.

        Raises:
            AuditFailure: If the event could not be written.
        """
        event_id = self._write(
            action,
            operation,
            outcome=outcome,
            target=target,
            criteria=criteria,
            recipient=recipient,
            ai=ai,
            parent_event_id=parent_event_id,
        )
        self.event_ids.append(event_id)
        return event_id

    def emit_refs(
        self,
        action: str,
        operation: str,
        *,
        kind: str,
        ids: Sequence[str],
        count: int,
        container: str | None = None,
        outcome: str = "ok",
        criteria: Mapping[str, object] | None = None,
        recipient: str | None = None,
        ai: AiCall | None = None,
        parent_event_id: str | None = None,
    ) -> str:
        """Write an operation event whose target lists ids, however many.

        The first chunk of ids goes on the operation's event; each further
        chunk goes on a continuation event (same action, operation
        ``<operation>.ids``, parented to the first), so no id is cut.

        Args:
            action: One of the audit library's actions.
            operation: What exactly happened, dotted and lowercase.
            kind: The target kind (``mixed`` for kind-prefixed ids).
            ids: Every id the operation touched.
            count: How many objects the operation touched.
            container: The enclosing object, if any.
            outcome: ``ok``, ``denied`` or ``error``.
            criteria: The caller's parameters (and, for writes, counts).
            recipient: Where data went, e.g. ``inference:chat``.
            ai: Model provenance, for ``ai_call`` events.
            parent_event_id: An earlier event of the same operation.

        Returns:
            The id of the operation's (first) event.

        Raises:
            AuditFailure: If an event could not be written.
        """
        chunks = id_chunks(list(ids))
        first = chunks[0] if chunks else []
        primary = self.emit(
            action,
            operation,
            outcome=outcome,
            target=Target(kind=kind, ids=first, count=count, container=container),
            criteria=criteria,
            recipient=recipient,
            ai=ai,
            parent_event_id=parent_event_id,
        )
        for chunk in chunks[1:]:
            self._write(
                action,
                f"{operation}.ids",
                outcome=outcome,
                target=Target(kind=kind, ids=chunk, count=len(chunk), container=container),
                parent_event_id=primary,
            )
        return primary

    def _write(
        self,
        action: str,
        operation: str,
        *,
        outcome: str,
        target: Target | None = None,
        criteria: Mapping[str, object] | None = None,
        recipient: str | None = None,
        ai: AiCall | None = None,
        parent_event_id: str | None = None,
    ) -> str:
        """Write one event through the trail's emitter.

        Args:
            action: One of the audit library's actions.
            operation: What exactly happened.
            outcome: ``ok``, ``denied`` or ``error``.
            target: What was touched, by reference.
            criteria: Parameters and counts.
            recipient: Where data went.
            ai: Model provenance.
            parent_event_id: An earlier event of the same operation.

        Returns:
            The event's id.

        Raises:
            AuditFailure: If the emitter could not write the event, or is not
                open.
        """
        try:
            event = self.trail.emitter.emit(
                action,
                operation,
                self.actor,
                outcome=outcome,
                request_id=self.request_id,
                parent_event_id=parent_event_id,
                purpose=self.purpose,
                target=target,
                criteria=criteria,
                recipient=recipient,
                ai=ai,
            )
        except (AuditWriteError, RuntimeError) as exc:
            raise AuditFailure("an audit event could not be written") from exc
        return str(event["event_id"])
