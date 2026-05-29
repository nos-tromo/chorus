"""Build OpenAI tool definitions from the registered retrieval tools.

The agent advertises each tool in :data:`chorus.tools.TOOLS` to the model using
its registered name, description, and input-model JSON schema. The model can
therefore only call tools that exist — it cannot invent operations or write
Cypher.
"""

from __future__ import annotations

from typing import Any

from chorus.tools import TOOLS


def tool_definitions() -> list[dict[str, Any]]:
    """Return the registered tools as OpenAI ``tools=[...]`` definitions.

    Returns:
        One ``{"type": "function", "function": {...}}`` entry per registered
        tool, where ``parameters`` is the tool input model's JSON schema
        (Pydantic emits aliases such as ``from``/``to``, which the tools
        accept on validation).
    """
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.input_model.model_json_schema(),
            },
        }
        for spec in TOOLS.values()
    ]
