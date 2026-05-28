"""UI for the `authors_connected_by_topic` tool."""

from __future__ import annotations

import os

import streamlit as st

from chorus.ui.client import ChorusClient

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
    return ChorusClient(
        base_url=os.environ.get("CHORUS_API_URL", "http://localhost:8000"),
        identity=os.environ.get("CHORUS_UI_IDENTITY", "dev"),
    )


client = _client()

st.title("authors connected by topic")
st.caption("1-hop only. Topic clustering is pending entity resolution — topics are alias surface forms for now.")

seed_author = st.text_input("Seed author handle or display name", value="")
min_overlap = st.number_input("Minimum shared topics", min_value=1, value=1, step=1)
limit = st.slider("Limit (per matched seed)", min_value=1, max_value=200, value=50)

if st.button("Find connected authors", disabled=not seed_author):
    payload: dict[str, object] = {
        "seed_author": seed_author,
        "min_overlap": int(min_overlap),
        "limit": limit,
    }
    try:
        result = client.call_tool("authors_connected_by_topic", payload)
    except Exception as exc:
        st.error(f"tool call failed: {exc}")
    else:
        groups = result.get("results", [])
        if not groups:
            st.info("no matching seed author")
        for group in groups:
            seed = group.get("seed", {})
            label = seed.get("display_name") or seed.get("handle") or seed.get("author_id")
            connected = group.get("connected", [])
            st.subheader(f"{label}  ·  {len(connected)} connected")
            if connected:
                st.dataframe(connected, use_container_width=True)
            else:
                st.info("no connected authors at this overlap threshold")
