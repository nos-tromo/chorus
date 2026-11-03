"""`@audited` decorator + tool registry shared types.

A tool function is wrapped so that every invocation writes exactly one
``query`` event (``operation="tool.<name>"``) to the data-protection audit
trail, plus id-continuation events when its result references more than one
event's worth of ids. The wrapped callable is invoked as
``run(driver, params, *, audit)`` with an
:class:`~chorus.audit.trail.AuditContext`; the tool function itself only
takes ``(driver, params)`` and never sees the trail.

The event's criteria are the tool's input parameters. Its target is
``kind="mixed"``: the result count, plus kind-prefixed references to what
the result disclosed, data subjects first — ``author:`` ids from
``audit_authors()``, ``entity:`` ids from ``audit_entities()``, ``post:``
uuids from ``audit_posts()`` on the output model (each optional), and the
count from ``audit_result_count()``. A tool that raises writes the event
with ``outcome="error"`` and no target, then re-raises. When ``query``
needs a stated purpose and the caller stated none, the call is refused with
a ``denied`` event before the tool runs.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import wraps
from typing import Any, TypeVar, cast

from pydantic import BaseModel

from chorus.audit.trail import AuditContext, subject_refs

OutT = TypeVar("OutT", bound=BaseModel)
FnT = TypeVar("FnT", bound=Callable[..., BaseModel])


def _refs(result: BaseModel, method: str) -> list[str]:
    """Return the ids an output model reports through one ``audit_*`` method.

    Args:
        result: The tool's output model.
        method: ``audit_authors``, ``audit_entities`` or ``audit_posts``.

    Returns:
        The reported ids, or an empty list when the model has no such method.
    """
    fn = getattr(result, method, None)
    if not callable(fn):
        return []
    return [str(value) for value in cast("Iterable[object]", fn())]


def audited(fn: Callable[..., OutT]) -> Callable[..., OutT]:
    """Wrap a tool function so each call writes one ``query`` audit event.

    The wrapper accepts ``(driver, params, *, audit)`` and calls
    ``fn(driver, params)``. The event is written after the result is built
    and before it is returned, so a failed audit write fails the call and
    no data leaves it.

    Args:
        fn: Tool function to wrap. Its name becomes the event's operation,
            ``tool.<name>``.

    Returns:
        The wrapped callable.
    """
    operation = f"tool.{fn.__name__}"

    @wraps(fn)
    def _wrapped(driver: Any, params: BaseModel, *, audit: AuditContext) -> OutT:
        criteria = params.model_dump(mode="json", by_alias=True)
        audit.require_purpose(("query",), operation, criteria)
        try:
            result = fn(driver, params)
        except Exception:
            audit.emit("query", operation, outcome="error", criteria=criteria)
            raise
        count_fn = getattr(result, "audit_result_count", None)
        ids = subject_refs(
            authors=_refs(result, "audit_authors"),
            entities=_refs(result, "audit_entities"),
            posts=_refs(result, "audit_posts"),
        )
        audit.emit_refs(
            "query",
            operation,
            kind="mixed",
            ids=ids,
            count=cast("int", count_fn()) if callable(count_fn) else len(ids),
            criteria=criteria,
        )
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
    is what ends up in the registry — the FastAPI router and the agent
    invoke ``TOOLS[name].run`` directly, expecting the audit event to fire.

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
