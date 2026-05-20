"""Cypher template loader.

Templates live in `chorus/queries/*.cypher`. Tools never inline Cypher;
they call `load_template(name)` and pass the result to `session.run`. This
keeps the queries auditable and lets reviewers diff Cypher independently
of the Python wrappers.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from chorus.utils.env_cfg import load_path_env


@lru_cache(maxsize=128)
def load_template(name: str) -> str:
    """Load a `.cypher` file from `chorus/queries/`. The `.cypher` suffix
    is optional in `name`."""
    if not name.endswith(".cypher"):
        name = f"{name}.cypher"
    path: Path = load_path_env().queries / name
    if not path.is_file():
        raise FileNotFoundError(f"Cypher template not found: {path}")
    return path.read_text(encoding="utf-8")
