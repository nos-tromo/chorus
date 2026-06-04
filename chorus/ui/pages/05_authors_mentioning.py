"""UI for the `authors_mentioning` tool."""

from __future__ import annotations

import os

import streamlit as st

from chorus.ui.client import ChorusClient

st.set_page_config(page_title="authors_mentioning — chorus")


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

st.title("authors mentioning an entity")
st.caption("Authors ranked by how many of their posts mention the entity.")

entity = st.text_input("Entity name or alias", value="")
limit = st.slider("Limit", min_value=1, max_value=200, value=50)

col_from, col_to = st.columns(2)
from_dt = col_from.text_input("From (ISO timestamp, optional)", value="")
to_dt = col_to.text_input("To (ISO timestamp, optional)", value="")

if st.button("Search", disabled=not entity):
    payload: dict[str, object] = {"entity": entity, "limit": limit}
    if from_dt:
        payload["from"] = from_dt
    if to_dt:
        payload["to"] = to_dt
    try:
        result = client.call_tool("authors_mentioning", payload)
    except Exception as exc:
        st.error(f"tool call failed: {exc}")
    else:
        authors = result.get("authors", [])
        st.write(f"{len(authors)} author(s)")
        if authors:
            st.dataframe(authors, use_container_width=True)
        else:
            st.info("no authors")
