"""Tool registry. Importing this package self-registers all built-in tools."""

# Self-register tools by importing their modules.
from chorus.tools import author_activity_summary, posts_mentioning  # noqa: F401
from chorus.tools._audit import TOOLS, ToolSpec, audited, register_tool

__all__ = ["TOOLS", "ToolSpec", "audited", "register_tool"]
