"""Validation errors reach the operational log without their submitted values."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from chorus.utils.log_redaction import redact_validation_errors

_MARKER = "MARKER-SUBMITTED-VALUE-0001"


class _Model(BaseModel):
    count: int
    name: str = Field(max_length=4)


def _errors(payload: dict[str, Any]) -> list[Any]:
    """Return the pydantic errors for ``payload`` against :class:`_Model`."""
    try:
        _Model.model_validate(payload)
    except ValidationError as exc:
        return list(exc.errors())
    raise AssertionError("expected a ValidationError")


def test_input_is_replaced_and_ctx_dropped() -> None:
    """Location, type and message survive; the submitted value and ctx do not."""
    redacted = redact_validation_errors(_errors({"count": _MARKER, "name": _MARKER}))

    assert _MARKER not in repr(redacted)
    assert [e["loc"] for e in redacted] == [("count",), ("name",)]
    assert [e["type"] for e in redacted] == ["int_parsing", "string_too_long"]
    assert all(e["input"] == "<redacted>" for e in redacted)
    assert all("ctx" not in e for e in redacted)
    assert all(e["msg"] for e in redacted)


def test_error_without_input_gains_no_marker() -> None:
    """An error that carried no input is passed through unchanged."""
    error = {"type": "missing", "loc": ("count",), "msg": "Field required"}
    assert redact_validation_errors([error]) == [error]


def test_non_dict_entries_become_unknown() -> None:
    """Anything that is not an error dict is reduced to a bare marker."""
    assert redact_validation_errors([f"free text {_MARKER}"]) == [{"type": "unknown"}]
