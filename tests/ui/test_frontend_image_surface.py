"""Guard test for the frontend Docker image surface.

The frontend is a React SPA served by nginx (``docker/Dockerfile.frontend``).
It is built by node and requires **no** Python ``chorus.*`` packages at runtime.
This test asserts that the Dockerfile contains no ``COPY chorus/`` lines — if one
is accidentally added, the image would be wrong in two ways: the layer would be
dead weight, and it would signal that someone conflated the old Streamlit image
(which did copy ``chorus/ui`` and ``chorus/utils``) with the new nginx image.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_DOCKERFILE = _REPO / "docker" / "Dockerfile.frontend"


def test_frontend_image_ships_no_python_chorus_packages() -> None:
    """The React SPA frontend image must not COPY any chorus/ Python package."""
    text = _DOCKERFILE.read_text(encoding="utf-8")
    copied = re.findall(r"COPY\s+chorus/(\w+)", text)
    assert not copied, (
        f"docker/Dockerfile.frontend unexpectedly COPYs chorus Python packages "
        f"{sorted(copied)} — the frontend is a pure nginx image and needs no Python source. "
        f"Remove those COPY lines (or update this test if the architecture changed)."
    )
