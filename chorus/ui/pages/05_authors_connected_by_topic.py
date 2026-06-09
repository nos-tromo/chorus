"""UI for the `authors_connected_by_topic` tool."""

from __future__ import annotations

import streamlit as st

from chorus.ui.client import ChorusClient
from chorus.utils.ui_strings import ui_string

st.set_page_config(page_title="authors_connected_by_topic — chorus")


@st.cache_resource
def _client() -> ChorusClient:
    """Construct (or return the cached) :class:`ChorusClient` for this page.

    Streamlit caches the result across reruns via ``@st.cache_resource``
    so we don't open a new HTTP pool on every script execution.

    Returns:
        A :class:`ChorusClient` bound to the configured API URL and
        identity, both pulled from the environment with development
        defaults (``http://localhost:8000`` and ``"dev"``).
    """
    return ChorusClient.from_env()


client = _client()

st.title(ui_string("authors_connected.title"))
st.caption(ui_string("authors_connected.caption"))

seed_author = st.text_input(ui_string("authors_connected.seed_author_input"), value="")
min_overlap = st.number_input(ui_string("authors_connected.min_overlap"), min_value=1, value=1, step=1)
limit = st.slider(ui_string("authors_connected.limit"), min_value=1, max_value=200, value=50)

if st.button(ui_string("authors_connected.find"), disabled=not seed_author):
    payload: dict[str, object] = {
        "seed_author": seed_author,
        "min_overlap": int(min_overlap),
        "limit": limit,
    }
    try:
        result = client.call_tool("authors_connected_by_topic", payload)
    except Exception as exc:
        st.error(ui_string("common.tool_call_failed").format(error=exc))
    else:
        groups = result.get("results", [])
        if not groups:
            st.info(ui_string("authors_connected.no_seed"))
        for group in groups:
            seed = group.get("seed", {})
            label = seed.get("display_name") or seed.get("handle") or seed.get("author_id")
            connected = group.get("connected", [])
            st.subheader(ui_string("authors_connected.connected_count").format(label=label, n=len(connected)))
            if connected:
                st.dataframe(connected, use_container_width=True)
            else:
                st.info(ui_string("authors_connected.none"))
