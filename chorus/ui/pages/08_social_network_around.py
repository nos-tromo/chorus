"""UI for the `social_network_around` tool — the author social ego network."""

from __future__ import annotations

import streamlit as st

from chorus.ui.client import ChorusClient
from chorus.ui.social_network_dot import to_dot
from chorus.utils.ui_strings import ui_string

st.set_page_config(page_title="social_network_around — chorus")


@st.cache_resource
def _client() -> ChorusClient:
    """Construct (or return the cached) :class:`ChorusClient` for this page.

    Returns:
        A :class:`ChorusClient` bound to the configured API URL and identity,
        both pulled from the environment with development defaults
        (``http://localhost:8000`` and ``"dev"``).
    """
    return ChorusClient.from_env()


client = _client()

st.title(ui_string("social.title"))
st.caption(ui_string("social.caption"))

author = st.text_input(ui_string("social.author_input"), value="")
depth = st.slider(ui_string("social.depth"), min_value=1, max_value=2, value=2)
col_a, col_b = st.columns(2)
limit = col_a.slider(ui_string("social.limit"), min_value=1, max_value=200, value=25)
second_ring_limit = col_b.slider(ui_string("social.second_ring_limit"), min_value=1, max_value=500, value=50)

if st.button(ui_string("social.build"), disabled=not author):
    payload: dict[str, object] = {
        "author": author,
        "depth": depth,
        "limit": limit,
        "second_ring_limit": second_ring_limit,
    }
    try:
        result = client.call_tool("social_network_around", payload)
    except Exception as exc:
        st.error(ui_string("common.tool_call_failed").format(error=exc))
    else:
        nodes = result.get("nodes", [])
        edges = result.get("edges", [])
        if not nodes:
            st.info(ui_string("social.empty"))
        else:
            follows = sum(1 for e in edges if e.get("kind") == "follows")
            friends = sum(1 for e in edges if e.get("kind") == "friends")
            st.write(
                ui_string("social.counts").format(n=len(nodes), edges=len(edges), follows=follows, friends=friends)
            )
            if result.get("truncated"):
                st.warning(ui_string("social.capped"))
            st.graphviz_chart(to_dot(result), use_container_width=True)
