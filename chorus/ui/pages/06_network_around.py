"""UI for the `network_around` tool — chorus's first drawn (network) result page."""

from __future__ import annotations

import os

import streamlit as st

from chorus.ui.client import ChorusClient
from chorus.ui.network_dot import to_dot

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

st.title("network around an entity")
st.caption(
    "Bipartite author-topic network. depth 1 = the authors who mention the entity; "
    "depth 2 also adds the other topics those authors mention. The view is capped "
    "by the limits below. Topics cluster by canonical entity once a resolution pass "
    "has run; on unresolved data they are raw alias surface forms."
)

entity = st.text_input("Entity name or alias", value="")
depth = st.slider("Depth", min_value=1, max_value=2, value=2)
col_a, col_b = st.columns(2)
limit = col_a.slider("Author limit", min_value=1, max_value=200, value=25)
topic_limit = col_b.slider("Topic limit (depth 2)", min_value=1, max_value=500, value=50)

if st.button("Build network", disabled=not entity):
    payload: dict[str, object] = {
        "entity": entity,
        "depth": depth,
        "limit": limit,
        "topic_limit": topic_limit,
    }
    try:
        result = client.call_tool("network_around", payload)
    except Exception as exc:
        st.error(f"tool call failed: {exc}")
    else:
        nodes = result.get("nodes", [])
        edges = result.get("edges", [])
        if not nodes:
            st.info("no network — the entity matched nothing")
        else:
            author_count = sum(1 for n in nodes if n.get("kind") == "author")
            topic_count = sum(1 for n in nodes if n.get("kind") == "topic")
            st.write(f"{len(nodes)} node(s): {author_count} author(s), {topic_count} topic(s); {len(edges)} edge(s)")
            if result.get("truncated"):
                st.warning("Capped view — raise the author/topic limits to see more of the network.")
            st.graphviz_chart(to_dot(result), use_container_width=True)
