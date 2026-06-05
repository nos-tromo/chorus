"""Unit tests for the network_around DOT builder (no Streamlit runtime)."""

from __future__ import annotations


def test_to_dot_highlights_seed_and_emits_edges() -> None:
    """The seed node is highlighted and one edge line is emitted per edge."""
    from chorus.ui.network_dot import to_dot

    result = {
        "seed": "Berlin",
        "seed_node_id": "topic:Berlin",
        "nodes": [
            {"id": "topic:Berlin", "kind": "topic", "label": "Berlin", "entity_id": None, "is_seed": True},
            {"id": "author:a", "kind": "author", "label": "anna", "entity_id": None, "is_seed": False},
            {"id": "topic:Paris", "kind": "topic", "label": "Paris", "entity_id": None, "is_seed": False},
        ],
        "edges": [
            {"source": "author:a", "target": "topic:Berlin", "weight": 2},
            {"source": "author:a", "target": "topic:Paris", "weight": 1},
        ],
        "truncated": False,
    }

    dot = to_dot(result)

    assert dot.startswith("digraph network {")
    assert dot.rstrip().endswith("}")
    # seed node highlighted (penwidth=2 marks it); the other topic is not
    seed_line = next(line for line in dot.splitlines() if '"topic:Berlin"' in line and "->" not in line)
    assert "penwidth=2" in seed_line and "#ffd54f" in seed_line
    # author drawn as a box
    author_line = next(line for line in dot.splitlines() if '"author:a"' in line and "->" not in line)
    assert "shape=box" in author_line
    # one edge line per input edge, weight surfaced as the label
    edge_lines = [line for line in dot.splitlines() if "->" in line]
    assert len(edge_lines) == 2
    assert any('label="2"' in line for line in edge_lines)


def test_to_dot_escapes_quotes_in_labels() -> None:
    """Double quotes in a label do not break the DOT string."""
    from chorus.ui.network_dot import to_dot

    result = {
        "nodes": [{"id": 'topic:say "hi"', "kind": "topic", "label": 'say "hi"', "entity_id": None, "is_seed": False}],
        "edges": [],
    }
    dot = to_dot(result)
    assert '\\"hi\\"' in dot


def test_to_dot_empty_network_is_valid() -> None:
    """An empty network still produces a valid, node-less digraph."""
    from chorus.ui.network_dot import to_dot

    dot = to_dot({"nodes": [], "edges": []})
    assert dot.startswith("digraph network {")
    assert dot.rstrip().endswith("}")
    assert "->" not in dot
