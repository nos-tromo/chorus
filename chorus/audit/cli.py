"""Run one audited CLI command in its own audit chain.

Every CLI command that processes personal data writes its own chain, with
``Actor(AUDIT_CLI_USER, "cli")`` as the actor and the purpose stated in
``AUDIT_CASE_REF`` / ``AUDIT_REASON``. Inside the backend container
(``docker compose exec``, ``AUDIT_OUTPUT=container``) the chain lands in the
backend's log next to the server's own.

Exit codes: a command that needs a stated purpose and got none, or a purpose
that is malformed, exits 2 with a one-line message; a failed audit write or
an unusable audit configuration exits 1. A malformed purpose is refused
before the chain starts, so it leaves no events at all.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from chorus.audit.trail import AuditContext, AuditFailure, AuditTrail, PurposeRequired
from chorus.utils.audit_event import Actor, Purpose
from chorus.utils.audit_runtime import PurposeError
from chorus.utils.env_cfg import load_audit_env, load_cli_purpose_env


def _fail(message: str, code: int) -> int:
    """Print a one-line error to stderr and return the exit code.

    Args:
        message: What went wrong, never a value the caller supplied.
        code: The exit code to return.

    Returns:
        ``code``.
    """
    print(f"error: {message}", file=sys.stderr)
    return code


def run_audited_command(body: Callable[[AuditContext], int]) -> int:
    """Run a CLI command body with an audit context of its own.

    Args:
        body: The command; receives the CLI caller's audit context and
            returns the process exit code.

    Returns:
        The body's exit code, or 2 for a missing or malformed purpose, or 1
        when the audit chain cannot be opened, written or closed.
    """
    try:
        case_ref, reason = load_cli_purpose_env()
    except PurposeError as exc:
        return _fail(f"the stated purpose is not accepted ({exc})", 2)
    try:
        cfg = load_audit_env()
        trail = AuditTrail.open(cfg.output, required=cfg.require_purpose)
    except (OSError, ValueError) as exc:
        return _fail(f"the audit trail cannot be opened ({exc})", 1)
    try:
        trail.start({})
    except AuditFailure:
        return _fail("the audit trail cannot be written; nothing was done", 1)

    purpose = Purpose(case_ref=case_ref, reason=reason) if case_ref or reason else None
    audit = trail.context(Actor(user=cfg.cli_user, via="cli"), purpose=purpose)
    code = 1
    try:
        code = body(audit)
    except PurposeRequired as exc:
        code = _fail(f"{exc.operation} needs a stated purpose: set AUDIT_CASE_REF and/or AUDIT_REASON", 2)
    except AuditFailure:
        code = _fail("an audit event could not be written; the command did not complete", 1)
    finally:
        try:
            trail.close()
        except AuditFailure:
            code = _fail("the audit trail could not be closed", 1)
    return code
