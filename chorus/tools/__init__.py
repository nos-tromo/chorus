"""Tool registry. Importing this package self-registers all built-in tools."""

from chorus.tools._audit import TOOLS, ToolSpec, audited, register_tool

# Self-register tools by importing their modules.
from chorus.tools import posts_mentioning  # noqa: F401


__all__ = ["TOOLS", "ToolSpec", "audited", "register_tool"]
