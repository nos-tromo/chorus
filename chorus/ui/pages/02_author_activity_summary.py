"""UI for the `author_activity_summary` tool."""

from __future__ import annotations

import os

import streamlit as st

from chorus.ui.client import ChorusClient

st.set_page_config(page_title="author_activity_summary — chorus")


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

st.title("author activity summary")
st.caption("Topic clustering is pending entity resolution — topics are alias surface forms for now.")

author = st.text_input("Author handle or display name", value="")

col_from, col_to = st.columns(2)
from_dt = col_from.text_input("From (ISO timestamp, optional)", value="")
to_dt = col_to.text_input("To (ISO timestamp, optional)", value="")

if st.button("Summarize", disabled=not author):
    payload: dict[str, object] = {"author": author}
    if from_dt:
        payload["from"] = from_dt
    if to_dt:
        payload["to"] = to_dt
    try:
        result = client.call_tool("author_activity_summary", payload)
    except Exception as exc:
        st.error(f"tool call failed: {exc}")
    else:
        summaries = result.get("summaries", [])
        st.write(f"{len(summaries)} author(s) matched")
        for su in summaries:
            label = su.get("display_name") or su.get("handle") or su.get("author_id")
            st.subheader(f"{label}  ·  {su.get('author_id')}")
            counts = {
                k: su.get(k)
                for k in (
                    "post_count",
                    "posting_count",
                    "comment_count",
                    "message_count",
                    "first_activity",
                    "last_activity",
                    "expected_reactions_total",
                    "collected_reactions_total",
                    "expected_comments_total",
                    "collected_comments_total",
                )
            }
            st.json(counts)
            top_topics = su.get("top_topics", [])
            if top_topics:
                st.dataframe(top_topics, use_container_width=True)
            else:
                st.info("no topics mentioned in range")
        if not summaries:
            st.info("no matching author")
