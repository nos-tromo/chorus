"""POST /tools/{name} — dispatch into the registered tool registry.

``GET /tools`` lists schemas only and is not audited. Every ``POST`` that
reaches a tool writes one ``query`` / ``tool.<name>`` audit event through
the ``@audited`` wrapper; an unknown tool (404) or an invalid payload (422)
runs no operation and writes none.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from pydantic import ValidationError

from chorus.api.audit import request_audit
from chorus.audit.trail import AuditContext
from chorus.tools import TOOLS
from chorus.utils.log_redaction import redact_validation_errors

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("")
def list_tools() -> list[dict[str, Any]]:
    """List every registered retrieval tool with its I/O schemas.

    The schemas are emitted as JSON Schema so the agent and any UI can
    introspect them without importing the Pydantic models directly.

    Returns:
        One dict per registered tool with keys ``name``,
        ``input_schema``, and ``output_schema``.
    """
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.input_model.model_json_schema(),
            "output_schema": spec.output_model.model_json_schema(),
        }
        for spec in TOOLS.values()
    ]


@router.post("/{name}")
def invoke_tool(
    name: str,
    payload: dict[str, Any],
    request: Request,
    audit: AuditContext = Depends(request_audit),  # noqa: B008 — FastAPI dependency marker
) -> dict[str, Any]:
    """Invoke a registered tool by name and return its result as JSON.

    Validates the payload against the tool's input model, then dispatches
    into ``TOOLS[name].run`` (which is the ``@audited`` wrapper, so an
    audit event is written on every call, before the result is returned).

    Args:
        name: Registered tool name from :data:`chorus.tools.TOOLS`.
        payload: Caller-supplied parameters to validate against the
            tool's input model.
        request: The active FastAPI request (used to access the shared
            driver on ``app.state``).
        audit: The caller's audit context.

    Returns:
        The tool's output model serialized as a JSON-compatible dict.

    Raises:
        HTTPException: ``404`` when no tool is registered under
            ``name``; ``422`` when ``payload`` fails input validation.
    """
    spec = TOOLS.get(name)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown tool: {name}")
    try:
        parsed = spec.input_model.model_validate(payload)
    except ValidationError as exc:
        logger.warning("Tool input validation failed for {}: {}", name, redact_validation_errors(exc.errors()))
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid request.") from exc
    out = spec.run(request.app.state.driver, parsed, audit=audit)
    return out.model_dump(mode="json")
