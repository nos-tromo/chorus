"""`@audited` decorator + tool registry shared types.

A tool function is wrapped so that every invocation produces exactly one
audit row via `AuditLogger.time_tool`. The decorator expects the function
to accept keyword arguments `user: str` and `audit: AuditLogger`, plus a
positional `driver` and a Pydantic-parsed `params` instance.

The wrapped function may update `slot.entities_touched` and
`slot.result_count` by returning a Pydantic model exposing
`audit_entities()` and `audit_result_count()` methods, or by writing to
`audit.slot` if it needs finer control.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from chorus.audit.logger import AuditLogger


class _Auditable(Protocol):
    """Structural interface for tool outputs that report audit metadata.

    Tools whose output models implement this protocol can populate the
    audit row with the entities they touched and the result count
    automatically; otherwise the row is written with zero counts.
    """

    def audit_entities(self) -> list[str]:
        """Return canonical entity ids the tool resolved during the call.

        Returns:
            Entity ids in arbitrary order. May be empty.
        """
        ...

    def audit_result_count(self) -> int:
        """Return the number of result rows the tool produced.

        Returns:
            A non-negative integer.
        """
        ...


OutT = TypeVar("OutT", bound=BaseModel)
FnT = TypeVar("FnT", bound=Callable[..., BaseModel])


def audited(fn: Callable[..., OutT]) -> Callable[..., OutT]:
    """Wrap a tool function with audit-log timing.

    The wrapped callable must accept ``(driver, params, *, user,
    audit)`` and return a Pydantic model. If the model implements
    :meth:`_Auditable.audit_entities` and
    :meth:`_Auditable.audit_result_count`, those values are written into
    the audit row; otherwise the row is written with zero result count
    and no entities touched. Exceptions raised by the wrapped function
    are re-raised after the audit row is written with
    ``status="error"``.

    Args:
        fn: Tool function to wrap. Its name is recorded as
            ``tool_name`` on the audit row.

    Returns:
        A wrapped callable with the same signature as ``fn``.
    """

    @wraps(fn)
    def _wrapped(driver: Any, params: BaseModel, *, user: str, audit: AuditLogger) -> OutT:
        with audit.time_tool(user, fn.__name__, params.model_dump(mode="json")) as slot:
            result = fn(driver, params, user=user, audit=audit)
            if isinstance(result, BaseModel) and hasattr(result, "audit_entities"):
                slot.entities_touched = result.audit_entities()
            if isinstance(result, BaseModel) and hasattr(result, "audit_result_count"):
                slot.result_count = result.audit_result_count()
            return result

    return _wrapped


@dataclass(frozen=True)
class ToolSpec:
    """Registry entry describing a single retrieval tool.

    Attributes:
        name: Unique tool name (matches the agent-facing identifier).
        input_model: Pydantic model the tool accepts as ``params``.
        output_model: Pydantic model the tool returns.
        run: The wrapped tool function itself.
        description: Short human/LLM-facing summary, surfaced to the agent
            as the OpenAI tool description. Defaults to the tool function's
            first docstring line.
    """

    name: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    run: Callable[..., BaseModel]
    description: str


TOOLS: dict[str, ToolSpec] = {}


def _first_line(doc: str | None) -> str:
    """Return the first non-empty, stripped line of ``doc`` (or ``""``).

    Args:
        doc: A docstring or ``None``.

    Returns:
        The first non-blank line with surrounding whitespace removed, or
        an empty string when ``doc`` is ``None`` or blank.
    """
    for line in (doc or "").strip().splitlines():
        if line.strip():
            return line.strip()
    return ""


def register_tool(
    *,
    name: str,
    input_model: type[BaseModel],
    output_model: type[BaseModel],
    description: str | None = None,
) -> Callable[[FnT], FnT]:
    """Decorator that registers a tool in the global :data:`TOOLS` registry.

    Apply this *outside* :func:`audited` so the wrapped (audited) function
    is what ends up in the registry — the FastAPI router invokes
    ``TOOLS[name].run`` directly, expecting audit logging to fire.

    Args:
        name: Unique registry key. Raises if already taken.
        input_model: Pydantic model the tool accepts as ``params``.
        output_model: Pydantic model the tool returns.
        description: Optional LLM-facing summary. When omitted, the tool
            function's first docstring line is used (``@audited`` preserves
            it via ``functools.wraps``).

    Returns:
        A decorator that registers the function and returns it unchanged.
    """

    def _register(fn: FnT) -> FnT:
        """Register ``fn`` in :data:`TOOLS` under the closure's ``name``.

        Args:
            fn: Tool function to register.

        Returns:
            ``fn`` unchanged, so the decorator is composable.

        Raises:
            RuntimeError: If a tool is already registered under ``name``.
        """
        if name in TOOLS:
            raise RuntimeError(f"duplicate tool name: {name}")
        TOOLS[name] = ToolSpec(
            name=name,
            input_model=input_model,
            output_model=output_model,
            run=fn,
            description=description or _first_line(fn.__doc__),
        )
        return fn

    return _register
