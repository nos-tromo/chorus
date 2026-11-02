"""Value-free views of errors for operational logs.

Operational logs go to container stdout, which is shipped to a central log
store that operators read. They must not carry personal data: no submitted
field values, no upstream row contents, no network-side identifiers. Helpers
here keep what an operator needs (where a value failed, and why) and drop the
value itself. The §76 BDSG audit log (``chorus.audit``) is a separate concern
and does not go through this module.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def redact_validation_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """Strip submitted values out of pydantic validation errors.

    A pydantic error carries the offending ``input`` alongside its location
    and type. In chorus that input is user or upstream content: an entity or
    author name in a tool call, a field of an ingested row. Logging
    ``exc.errors()`` as-is would put it in the operational log. The location
    and the error type are what an operator needs; the value is not.

    Args:
        errors: ``ValidationError.errors()`` output.

    Returns:
        The same errors with ``input`` replaced by a marker and ``ctx``
        dropped (it can embed the value too).
    """
    redacted: list[dict[str, Any]] = []
    for error in errors:
        if not isinstance(error, dict):
            redacted.append({"type": "unknown"})
            continue
        safe = {key: value for key, value in error.items() if key not in {"input", "ctx"}}
        if "input" in error:
            safe["input"] = "<redacted>"
        redacted.append(safe)
    return redacted
