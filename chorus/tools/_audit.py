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

from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Protocol, TypeVar

from pydantic import BaseModel

from chorus.audit.logger import AuditLogger


class _Auditable(Protocol):
    def audit_entities(self) -> list[str]: ...
    def audit_result_count(self) -> int: ...


OutT = TypeVar("OutT", bound=BaseModel)
FnT = TypeVar("FnT", bound=Callable[..., BaseModel])


def audited(fn: Callable[..., OutT]) -> Callable[..., OutT]:
    """Wrap a tool function with audit-log timing.

    The wrapped callable must accept (driver, params, *, user, audit) and
    return a Pydantic model. If the model implements `audit_entities` and
    `audit_result_count`, those values are recorded; otherwise the row is
    written with zero result_count and no entities_touched.
    """

    @wraps(fn)
    def _wrapped(
        driver: Any, params: BaseModel, *, user: str, audit: AuditLogger
    ) -> OutT:
        with audit.time_tool(user, fn.__name__, params.model_dump(mode="json")) as slot:
            result = fn(driver, params, user=user, audit=audit)
            if isinstance(result, BaseModel) and hasattr(result, "audit_entities"):
                slot.entities_touched = result.audit_entities()  # type: ignore[attr-defined]
            if isinstance(result, BaseModel) and hasattr(result, "audit_result_count"):
                slot.result_count = result.audit_result_count()  # type: ignore[attr-defined]
            return result

    return _wrapped


@dataclass(frozen=True)
class ToolSpec:
    name: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    run: Callable[..., BaseModel]


TOOLS: dict[str, ToolSpec] = {}


def register_tool(
    *,
    name: str,
    input_model: type[BaseModel],
    output_model: type[BaseModel],
) -> Callable[[FnT], FnT]:
    def _register(fn: FnT) -> FnT:
        if name in TOOLS:
            raise RuntimeError(f"duplicate tool name: {name}")
        TOOLS[name] = ToolSpec(
            name=name, input_model=input_model, output_model=output_model, run=fn
        )
        return fn

    return _register
