"""The frontend Docker image must ship every chorus package its UI imports.

`docker/Dockerfile.frontend` copies a deliberately minimal subset of the chorus
package (just `chorus/ui` + `chorus/utils` today). If a UI module imports a
chorus subpackage the Dockerfile does not COPY, the image boots but every page
crashes at import with ``ModuleNotFoundError`` — exactly what happened when
localization added ``from chorus.utils.ui_strings import ui_string`` to the
pages while the Dockerfile still copied only ``chorus/ui``.

This test ties the two together: the set of ``chorus.<pkg>`` the UI imports must
be a subset of the ``COPY chorus/<pkg>`` lines in the frontend Dockerfile. Drift
fails here instead of in a deployed container.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_DOCKERFILE = _REPO / "docker" / "Dockerfile.frontend"
_UI_DIR = _REPO / "chorus" / "ui"


def _shipped_chorus_packages() -> set[str]:
    """Top-level ``chorus/<pkg>`` paths the frontend Dockerfile COPYs."""
    text = _DOCKERFILE.read_text(encoding="utf-8")
    return set(re.findall(r"COPY\s+chorus/(\w+)", text))


def _ui_imported_chorus_packages() -> set[str]:
    """Top-level chorus subpackages imported anywhere under ``chorus/ui``."""
    pattern = re.compile(r"(?:from|import)\s+chorus\.(\w+)")
    used: set[str] = set()
    for src in _UI_DIR.rglob("*.py"):
        used |= set(pattern.findall(src.read_text(encoding="utf-8")))
    return used


def test_frontend_image_ships_every_chorus_package_the_ui_imports() -> None:
    """Every chorus subpackage the UI imports is COPYed into the frontend image."""
    shipped = _shipped_chorus_packages()
    needed = _ui_imported_chorus_packages()
    missing = needed - shipped
    assert not missing, (
        f"chorus/ui imports {sorted(missing)} but docker/Dockerfile.frontend does not "
        f"COPY them — the image will ModuleNotFoundError at page load. Add "
        f"`COPY chorus/<pkg> /app/chorus/<pkg>` (or refactor the UI). shipped={sorted(shipped)}"
    )
