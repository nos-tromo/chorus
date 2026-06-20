"""Guard tests for the frontend Dockerfile and nginx proxy config.

Verifies that:
- base images in ``docker/Dockerfile.frontend`` are pinned to full SHA-256 digests,
- the templated upload-size variable is wired end-to-end (Dockerfile ENV →
  ``default.conf.template`` → ``client_max_body_size``), and
- the nginx config contains the SPA fallback and all required API proxy prefixes.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_frontend_base_images_are_digest_pinned() -> None:
    """Every node/nginx base image in the frontend Dockerfile must carry a full sha256 digest."""
    df = (REPO / "docker" / "Dockerfile.frontend").read_text()
    froms = re.findall(r"(?m)^\s*(?:ARG\s+\w+=|FROM\s+)\S*(node|nginx)\S*", df)
    assert froms, "expected node + nginx base images"
    for line in df.splitlines():
        if ("node:" in line or "nginx:" in line) and ("FROM" in line or "ARG" in line):
            # full 64-hex digest required — `@sha256:<PLACEHOLDER>` must not pass
            assert re.search(r"@sha256:[0-9a-f]{64}\b", line), (
                f"base image not digest-pinned with a full sha256: {line}"
            )


def test_frontend_templated_upload_limit() -> None:
    """The upload limit var must be set in the Dockerfile and consumed in the nginx template."""
    df = (REPO / "docker" / "Dockerfile.frontend").read_text()
    assert "CHORUS_CLIENT_MAX_BODY_SIZE" in df
    assert "templates/default.conf.template" in df
    conf = (REPO / "frontend" / "nginx" / "default.conf.template").read_text()
    assert "client_max_body_size ${CHORUS_CLIENT_MAX_BODY_SIZE};" in conf


def test_frontend_spa_fallback_and_api_proxy() -> None:
    """The nginx config must have the SPA fallback and proxy all backend API prefixes."""
    conf = (REPO / "frontend" / "nginx" / "default.conf.template").read_text()
    assert "try_files $uri /index.html" in conf
    for prefix in ("/config", "/tools", "/agent", "/ingestion", "/health"):
        assert prefix in conf


def test_frontend_image_has_no_python_packages() -> None:
    """The React SPA frontend image must not COPY any chorus/ Python package.

    The frontend is served by nginx and requires no Python chorus.* packages
    at runtime. Any COPY of chorus/ into the frontend image would be dead
    weight and would signal confusion with the old Streamlit image.
    """
    text = (REPO / "docker" / "Dockerfile.frontend").read_text(encoding="utf-8")
    copied = re.findall(r"COPY\s+chorus/(\w+)", text)
    assert not copied, (
        f"docker/Dockerfile.frontend unexpectedly COPYs chorus Python packages "
        f"{sorted(copied)} — the frontend is a pure nginx image and needs no Python source. "
        f"Remove those COPY lines (or update this test if the architecture changed)."
    )
