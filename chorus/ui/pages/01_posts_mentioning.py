"""UI for the `posts_mentioning` tool."""

from __future__ import annotations

import os

import streamlit as st

from chorus.ui.client import ChorusClient
from chorus.utils.ui_strings import ui_string

st.set_page_config(page_title="posts_mentioning — chorus")


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
    return ChorusClient(
        base_url=os.environ.get("CHORUS_API_URL", "http://localhost:8000"),
        identity=os.environ.get("CHORUS_UI_IDENTITY", "dev"),
    )


client = _client()

st.title(ui_string("posts.title"))

entity = st.text_input(ui_string("common.entity_input"), value="")
limit = st.slider(ui_string("common.limit"), min_value=1, max_value=200, value=50)

col_from, col_to = st.columns(2)
from_dt = col_from.text_input(ui_string("common.from_ts"), value="")
to_dt = col_to.text_input(ui_string("common.to_ts"), value="")

if st.button(ui_string("common.search"), disabled=not entity):
    payload: dict[str, object] = {"entity": entity, "limit": limit}
    if from_dt:
        payload["from"] = from_dt
    if to_dt:
        payload["to"] = to_dt
    try:
        result = client.call_tool("posts_mentioning", payload)
    except Exception as exc:
        st.error(ui_string("common.tool_call_failed").format(error=exc))
    else:
        hits = result.get("hits", [])
        st.write(ui_string("posts.hits").format(n=len(hits)))
        if hits:
            st.dataframe(hits, use_container_width=True)
        else:
            st.info(ui_string("posts.no_hits"))
