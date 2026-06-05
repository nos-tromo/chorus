"""UI for the `network_around` tool — chorus's first drawn (network) result page."""

from __future__ import annotations

import os

import streamlit as st

from chorus.ui.client import ChorusClient
from chorus.ui.network_dot import to_dot
from chorus.utils.ui_strings import ui_string

st.set_page_config(page_title="network_around — chorus")


@st.cache_resource
def _client() -> ChorusClient:
    """Construct (or return the cached) :class:`ChorusClient` for this page.

    Returns:
        A :class:`ChorusClient` bound to the configured API URL and identity,
        both pulled from the environment with development defaults
        (``http://localhost:8000`` and ``"dev"``).
    """
    return ChorusClient(
        base_url=os.environ.get("CHORUS_API_URL", "http://localhost:8000"),
        identity=os.environ.get("CHORUS_UI_IDENTITY", "dev"),
    )


client = _client()

st.title(ui_string("network.title"))
st.caption(ui_string("network.caption"))

entity = st.text_input(ui_string("common.entity_input"), value="")
depth = st.slider(ui_string("network.depth"), min_value=1, max_value=2, value=2)
col_a, col_b = st.columns(2)
limit = col_a.slider(ui_string("network.author_limit"), min_value=1, max_value=200, value=25)
topic_limit = col_b.slider(ui_string("network.topic_limit"), min_value=1, max_value=500, value=50)

if st.button(ui_string("network.build"), disabled=not entity):
    payload: dict[str, object] = {
        "entity": entity,
        "depth": depth,
        "limit": limit,
        "topic_limit": topic_limit,
    }
    try:
        result = client.call_tool("network_around", payload)
    except Exception as exc:
        st.error(ui_string("common.tool_call_failed").format(error=exc))
    else:
        nodes = result.get("nodes", [])
        edges = result.get("edges", [])
        if not nodes:
            st.info(ui_string("network.empty"))
        else:
            author_count = sum(1 for n in nodes if n.get("kind") == "author")
            topic_count = sum(1 for n in nodes if n.get("kind") == "topic")
            st.write(
                ui_string("network.counts").format(
                    n=len(nodes), authors=author_count, topics=topic_count, edges=len(edges)
                )
            )
            if result.get("truncated"):
                st.warning(ui_string("network.capped"))
            st.graphviz_chart(to_dot(result), use_container_width=True)
