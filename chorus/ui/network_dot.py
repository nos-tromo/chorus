"""Build a Graphviz DOT string from a ``network_around`` result.

Kept separate from the Streamlit page so it is a pure, importable, unit-testable
function with no Streamlit (or rendering) dependency. The page passes the DOT
string to ``st.graphviz_chart``, which renders it client-side with the viz.js
bundle Streamlit already ships — no system ``graphviz`` binary and no runtime
network call, satisfying the airgap constraint.
"""

from __future__ import annotations

from typing import Any

# Styling. Authors are boxes, topics are ellipses; the seed topic is highlighted.
_AUTHOR_FILL = "#90caf9"
_TOPIC_FILL = "#c8e6c9"
_SEED_FILL = "#ffd54f"
_MAX_PENWIDTH = 6.0


def _escape(text: str) -> str:
    """Escape a string for use inside a DOT double-quoted id/label.

    Args:
        text: Raw label or id text.

    Returns:
        The text with backslashes and double quotes backslash-escaped.
    """
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _penwidth(weight: int) -> float:
    """Map an edge weight to a bounded DOT pen width.

    Args:
        weight: Distinct mentioning-post count for the edge.

    Returns:
        A pen width in ``[1.0, _MAX_PENWIDTH]`` that grows with ``weight``.
    """
    return min(1.0 + 0.5 * max(weight - 1, 0), _MAX_PENWIDTH)


def to_dot(result: dict[str, Any]) -> str:
    """Render a ``network_around`` JSON result as a Graphviz DOT digraph.

    Args:
        result: The tool's JSON output — ``{"nodes": [...], "edges": [...],
            ...}``. Each node is ``{id, kind, label, entity_id, is_seed}``;
            each edge is ``{source, target, weight}``.

    Returns:
        A DOT ``digraph`` string suitable for ``st.graphviz_chart``. An empty
        network still yields a valid (node-less) digraph.
    """
    lines: list[str] = [
        "digraph network {",
        "  rankdir=LR;",
        '  node [style=filled, fontname="sans-serif"];',
        '  edge [color="#9e9e9e"];',
    ]

    for node in result.get("nodes", []):
        node_id = _escape(str(node["id"]))
        label = _escape(str(node.get("label", node["id"])))
        if node.get("is_seed"):
            shape, fill, extra = "ellipse", _SEED_FILL, ", penwidth=2"
        elif node.get("kind") == "author":
            shape, fill, extra = "box", _AUTHOR_FILL, ""
        else:
            shape, fill, extra = "ellipse", _TOPIC_FILL, ""
        lines.append(f'  "{node_id}" [label="{label}", shape={shape}, fillcolor="{fill}"{extra}];')

    for edge in result.get("edges", []):
        source = _escape(str(edge["source"]))
        target = _escape(str(edge["target"]))
        weight = int(edge.get("weight", 1))
        lines.append(f'  "{source}" -> "{target}" [penwidth={_penwidth(weight):.1f}, label="{weight}"];')

    lines.append("}")
    return "\n".join(lines)
