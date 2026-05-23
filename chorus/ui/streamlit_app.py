"""Streamlit landing page.

Run with `uv run streamlit run chorus/ui/streamlit_app.py`.

CHORUS_API_URL points at the FastAPI service (default http://localhost:8000).
CHORUS_UI_IDENTITY supplies the principal header for dev (in prod the
reverse proxy injects X-Auth-User).
"""

from __future__ import annotations

import os

import streamlit as st

from chorus.ui.client import ChorusClient


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
    return ChorusClient(
        base_url=os.environ.get("CHORUS_API_URL", "http://localhost:8000"),
        identity=os.environ.get("CHORUS_UI_IDENTITY", "dev"),
    )


client = _client()

st.title("chorus")
st.caption("GraphRAG for social network analysis")

col_h, col_t = st.columns(2)
with col_h:
    st.subheader("backend health")
    try:
        st.json(client.health())
    except Exception as exc:  # noqa: BLE001
        st.error(f"unreachable: {exc}")

with col_t:
    st.subheader("registered tools")
    try:
        tools = client.list_tools()
        st.write([t["name"] for t in tools])
    except Exception as exc:  # noqa: BLE001
        st.error(f"unreachable: {exc}")

st.info("Pick a tool from the sidebar (left) to explore the graph.")
