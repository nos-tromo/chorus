"""Tool registry metadata: every tool carries an LLM-facing description."""

from __future__ import annotations


def test_all_tools_have_descriptions() -> None:
    """Each registered tool exposes a non-empty description string.

    The agent surfaces these as OpenAI tool definitions, so a missing
    description would leave the model guessing what a tool does.
    """
    from chorus.tools import TOOLS

    assert TOOLS, "no tools registered"
    for name, spec in TOOLS.items():
        assert isinstance(spec.description, str) and spec.description.strip(), f"tool {name!r} has no description"
