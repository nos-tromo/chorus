"""POST /agent/query — natural-language agent over the tool registry.

The agent selects and calls the registered retrieval tools to answer a
free-text question; it never writes Cypher. The server is stateless — the
client sends the visible conversation so far on each request.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel

from chorus.agent.loop import AgentInferenceError, run_agent
from chorus.api.deps import RequestContext, resolve_context
from chorus.utils.env_cfg import load_agent_env, load_language_env

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentMessage(BaseModel):
    """One visible chat turn supplied by the caller.

    Attributes:
        role: ``"user"`` or ``"assistant"``.
        content: The turn's text.
    """

    role: Literal["user", "assistant"]
    content: str


class AgentQueryIn(BaseModel):
    """Request body for ``POST /agent/query``.

    Attributes:
        messages: The visible conversation so far; the last entry should be
            the new user turn.
    """

    messages: list[AgentMessage]


@router.post("/query")
def agent_query(
    body: AgentQueryIn,
    ctx: RequestContext = Depends(resolve_context),  # noqa: B008 — FastAPI DI marker
) -> dict[str, Any]:
    """Run the agent over the conversation and return its answer and trace.

    Validates the body, then dispatches into
    :func:`chorus.agent.loop.run_agent` with the active project's driver
    and audit logger (ADR 0017). The turn is recorded as a parent
    ``agent_query`` audit row; each tool the agent calls writes its own row.

    Args:
        body: The visible conversation turns (server is stateless).
        ctx: Per-request context (principal, active project, that
            project's driver + audit logger).

    Returns:
        The agent result serialized as JSON: ``answer``, ``trace``, and
        ``truncated``.

    Raises:
        HTTPException: ``502`` when the inference backend call fails — it is
            unreachable/misconfigured, or the model rejects the tool-calling
            request (likely no function-calling support).
    """
    cfg = load_agent_env()
    try:
        result = run_agent(
            ctx.driver,
            ctx.audit,
            user=ctx.user,
            project=ctx.project,
            messages=[m.model_dump() for m in body.messages],
            max_iterations=cfg.max_tool_iterations,
            model=cfg.model,
            language=load_language_env().code,
            tool_message_max_items=cfg.tool_message_max_items,
            tool_message_max_chars=cfg.tool_message_max_chars,
        )
    except AgentInferenceError as exc:
        logger.exception("Agent query failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Agent request failed.") from exc
    return result.model_dump(mode="json")
