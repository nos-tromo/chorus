"""POST /tools/{name} — dispatch into the registered tool registry."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import ValidationError

from chorus.api.deps import RequestContext, resolve_context
from chorus.tools import TOOLS

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
    ctx: RequestContext = Depends(resolve_context),  # noqa: B008 — FastAPI DI marker
) -> dict[str, Any]:
    """Invoke a registered tool by name and return its result as JSON.

    Validates the payload against the tool's input model, then dispatches
    into ``TOOLS[name].run`` (which is the ``@audited`` wrapper, so a
    row is written to the audit log on every call) against the active
    project's driver and audit log (ADR 0017).

    Args:
        name: Registered tool name from :data:`chorus.tools.TOOLS`.
        payload: Caller-supplied parameters to validate against the
            tool's input model.
        ctx: Per-request context (principal, active project, that
            project's driver + audit logger).

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
        logger.warning(f"Tool input validation failed: {exc.errors()}")
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid request.") from exc
    out = spec.run(
        ctx.driver,
        parsed,
        user=ctx.user,
        audit=ctx.audit,
        project=ctx.project,
    )
    return out.model_dump(mode="json")
