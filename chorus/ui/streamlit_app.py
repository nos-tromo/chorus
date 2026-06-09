"""Streamlit landing page.

Run with `uv run streamlit run chorus/ui/streamlit_app.py`.

CHORUS_API_URL points at the FastAPI service (default http://localhost:8000).
CHORUS_UI_IDENTITY supplies the principal header for dev (in prod the
reverse proxy injects X-Auth-User).
"""

from __future__ import annotations

import streamlit as st

from chorus.ui.client import ChorusClient
from chorus.utils.ui_strings import ui_string

st.set_page_config(page_title="chorus", layout="wide")


@st.cache_resource
def _client() -> ChorusClient:
    """Construct (or return the cached) :class:`ChorusClient` for this session.

    Streamlit caches the result across reruns via ``@st.cache_resource``
    so we don't open a new HTTP pool on every script execution.

    Returns:
        A :class:`ChorusClient` bound to the configured API URL and
        identity, both pulled from the environment with development
        defaults (``http://localhost:8000`` and ``"dev"``).
    """
    return ChorusClient.from_env()


client = _client()

st.title("chorus")
st.caption(ui_string("landing.caption"))

# Status bar: is the frontend data-ingestion feature enabled on the backend?
# Sourced from /ingestion/feature (the single source of truth). On an
# unreachable backend the health panel below surfaces the error, so the banner
# stays quiet rather than double-reporting.
try:
    _ingestion_enabled: bool | None = bool(client.ingestion_status().get("enabled", False))
except Exception:
    _ingestion_enabled = None
if _ingestion_enabled is True:
    st.success(ui_string("landing.ingestion_on"))
elif _ingestion_enabled is False:
    st.info(ui_string("landing.ingestion_off"))

col_h, col_t = st.columns(2)
with col_h:
    st.subheader(ui_string("landing.backend_health"))
    try:
        st.json(client.health())
    except Exception as exc:
        st.error(ui_string("common.unreachable").format(error=exc))

with col_t:
    st.subheader(ui_string("landing.registered_tools"))
    try:
        tools = client.list_tools()
        st.write([t["name"] for t in tools])
    except Exception as exc:
        st.error(ui_string("common.unreachable").format(error=exc))

st.info(ui_string("landing.pick_tool"))
