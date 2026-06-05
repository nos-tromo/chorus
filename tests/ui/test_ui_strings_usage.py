"""Every ``ui_string('…')`` key referenced by a UI page must exist in the catalog.

Guards the mechanical literal→catalog migration: a typo'd key in a page would
otherwise only surface as a ``KeyError`` when that branch renders.
"""

from __future__ import annotations

import re
from pathlib import Path


def test_every_ui_string_key_used_in_the_ui_exists() -> None:
    """All ``ui_string("…")`` keys used by the UI are defined in ``UI_STRINGS``."""
    from chorus.utils.ui_strings import UI_STRINGS

    ui_dir = Path(__file__).resolve().parents[2] / "chorus" / "ui"
    sources = [ui_dir / "streamlit_app.py", *sorted((ui_dir / "pages").glob("*.py"))]
    pattern = re.compile(r'ui_string\(\s*"([^"]+)"\s*\)')
    used: set[str] = set()
    for src in sources:
        used |= set(pattern.findall(src.read_text(encoding="utf-8")))

    assert used, "expected to find ui_string(...) usages in the UI"
    missing = sorted(k for k in used if k not in UI_STRINGS["en"])
    assert not missing, f"UI references unknown ui_string keys: {missing}"
