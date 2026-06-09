"""Build a Graphviz DOT string from a ``social_network_around`` result.

Kept separate from the Streamlit page so it is a pure, importable, unit-testable
function with no Streamlit (or rendering) dependency. The page passes the DOT
string to ``st.graphviz_chart``, which renders it client-side with the viz.js
bundle Streamlit already ships — no system ``graphviz`` binary and no runtime
network call, satisfying the airgap constraint.

Every node is an author, coloured by ring (the seed is highlighted). Follows
edges are directed (arrowhead); friends edges are drawn with ``dir=none`` (no
arrowhead) and dashed, so the picture distinguishes "who follows whom" from a
mutual friendship.
"""

from __future__ import annotations

from typing import Any

# Styling. The seed is highlighted; ring 1 and ring 2 get distinct fills.
_SEED_FILL = "#ffd54f"
_RING1_FILL = "#90caf9"
_RING2_FILL = "#b0bec5"
_OTHER_FILL = "#cfd8dc"
_EDGE_COLOR = "#9e9e9e"


def _escape(text: str) -> str:
    """Escape a string for use inside a DOT double-quoted id/label.

    Args:
        text: Raw label or id text.

    Returns:
        The text with backslashes and double quotes backslash-escaped.
    """
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _is_seed(node: dict[str, Any]) -> bool:
    """Return whether ``node`` is the seed (``is_seed`` flag or ring 0)."""
    return bool(node.get("is_seed")) or node.get("ring") == 0


def _fill(node: dict[str, Any]) -> str:
    """Pick a fill colour for an author node from its ring.

    Args:
        node: A node map ``{id, label, ring, is_seed}``.

    Returns:
        A hex colour string: the seed highlight, the ring-1 or ring-2 fill, or a
        neutral fallback for any other ring value.
    """
    if _is_seed(node):
        return _SEED_FILL
    if node.get("ring") == 1:
        return _RING1_FILL
    if node.get("ring") == 2:
        return _RING2_FILL
    return _OTHER_FILL


def to_dot(result: dict[str, Any]) -> str:
    """Render a ``social_network_around`` JSON result as a Graphviz DOT digraph.

    Args:
        result: The tool's JSON output — ``{"nodes": [...], "edges": [...],
            ...}``. Each node is ``{id, label, ring, is_seed}``; each edge is
            ``{source, target, kind, directed}``.

    Returns:
        A DOT ``digraph`` string suitable for ``st.graphviz_chart``. An empty
        network still yields a valid (node-less) digraph.
    """
    lines: list[str] = [
        "digraph social {",
        "  rankdir=LR;",
        '  node [style=filled, fontname="sans-serif"];',
        f'  edge [color="{_EDGE_COLOR}"];',
    ]

    for node in result.get("nodes", []):
        node_id = _escape(str(node["id"]))
        label = _escape(str(node.get("label", node["id"])))
        extra = ", penwidth=2" if _is_seed(node) else ""
        lines.append(f'  "{node_id}" [label="{label}", shape=box, fillcolor="{_fill(node)}"{extra}];')

    for edge in result.get("edges", []):
        source = _escape(str(edge["source"]))
        target = _escape(str(edge["target"]))
        kind = _escape(str(edge.get("kind", "")))
        # Follows keep the arrowhead; friends are undirected (dir=none) and dashed.
        style = f'label="{kind}"' if edge.get("directed") else f'label="{kind}", dir=none, style=dashed'
        lines.append(f'  "{source}" -> "{target}" [{style}];')

    lines.append("}")
    return "\n".join(lines)
