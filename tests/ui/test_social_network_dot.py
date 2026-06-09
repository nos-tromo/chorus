"""Unit tests for the social_network_around DOT builder (no Streamlit runtime)."""

from __future__ import annotations


def _result() -> dict[str, object]:
    """A small ego-network result: seed + two ring-1 authors, one follows + one friends edge."""
    return {
        "seed": "seedh",
        "seed_node_id": "author:seed",
        "nodes": [
            {"id": "author:seed", "label": "seedh", "ring": 0, "is_seed": True},
            {"id": "author:a", "label": "ah", "ring": 1, "is_seed": False},
            {"id": "author:b", "label": "bh", "ring": 1, "is_seed": False},
        ],
        "edges": [
            {"source": "author:seed", "target": "author:a", "kind": "follows", "directed": True},
            {"source": "author:b", "target": "author:seed", "kind": "friends", "directed": False},
        ],
        "truncated": False,
    }


def test_to_dot_highlights_seed_and_draws_author_boxes() -> None:
    """The seed is highlighted and authors are drawn as boxes."""
    from chorus.ui.social_network_dot import to_dot

    dot = to_dot(_result())

    assert dot.startswith("digraph social {")
    assert dot.rstrip().endswith("}")
    seed_line = next(line for line in dot.splitlines() if '"author:seed"' in line and "->" not in line)
    assert "penwidth=2" in seed_line and "#ffd54f" in seed_line
    author_line = next(line for line in dot.splitlines() if '"author:a"' in line and "->" not in line)
    assert "shape=box" in author_line


def test_to_dot_follows_edge_has_arrow_friends_edge_does_not() -> None:
    """A follows edge is directed (arrowhead); a friends edge is drawn with dir=none."""
    from chorus.ui.social_network_dot import to_dot

    dot = to_dot(_result())

    follows_line = next(line for line in dot.splitlines() if '"author:seed" -> "author:a"' in line)
    friends_line = next(line for line in dot.splitlines() if '"author:b" -> "author:seed"' in line)
    assert "dir=none" not in follows_line  # directed: keep the arrowhead
    assert "dir=none" in friends_line  # undirected: no arrowhead

    edge_lines = [line for line in dot.splitlines() if "->" in line]
    assert len(edge_lines) == 2


def test_to_dot_rings_styled_distinctly() -> None:
    """Ring-1 and ring-2 authors get different fill colours."""
    from chorus.ui.social_network_dot import to_dot

    result = {
        "nodes": [
            {"id": "author:seed", "label": "s", "ring": 0, "is_seed": True},
            {"id": "author:r1", "label": "r1", "ring": 1, "is_seed": False},
            {"id": "author:r2", "label": "r2", "ring": 2, "is_seed": False},
        ],
        "edges": [],
    }
    dot = to_dot(result)
    r1_line = next(line for line in dot.splitlines() if '"author:r1"' in line and "->" not in line)
    r2_line = next(line for line in dot.splitlines() if '"author:r2"' in line and "->" not in line)

    def _fill(line: str) -> str:
        return line.split('fillcolor="', 1)[1].split('"', 1)[0]

    assert _fill(r1_line) != _fill(r2_line)


def test_to_dot_escapes_quotes_in_labels() -> None:
    """Double quotes in a label do not break the DOT string."""
    from chorus.ui.social_network_dot import to_dot

    result = {
        "nodes": [{"id": 'author:say "hi"', "label": 'say "hi"', "ring": 0, "is_seed": True}],
        "edges": [],
    }
    dot = to_dot(result)
    assert '\\"hi\\"' in dot


def test_to_dot_empty_network_is_valid() -> None:
    """An empty network still produces a valid, node-less digraph."""
    from chorus.ui.social_network_dot import to_dot

    dot = to_dot({"nodes": [], "edges": []})
    assert dot.startswith("digraph social {")
    assert dot.rstrip().endswith("}")
    assert "->" not in dot
