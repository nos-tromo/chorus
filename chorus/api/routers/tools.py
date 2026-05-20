"""POST /tools/{name} — dispatch into the registered tool registry."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError

from chorus.api.auth.principal import resolve_principal
from chorus.tools import TOOLS


router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("")
def list_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": spec.name,
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
    user: str = Depends(resolve_principal),
) -> dict[str, Any]:
    spec = TOOLS.get(name)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown tool: {name}")
    try:
        parsed = spec.input_model.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, exc.errors())
    out = spec.run(
        request.app.state.driver,
        parsed,
        user=user,
        audit=request.app.state.audit,
    )
    return out.model_dump(mode="json")
