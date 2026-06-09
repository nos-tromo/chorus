"""UI for the `author_activity_summary` tool."""

from __future__ import annotations

import streamlit as st

from chorus.ui.client import ChorusClient
from chorus.utils.ui_strings import ui_string

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
    return ChorusClient.from_env()


client = _client()

st.title(ui_string("author_activity.title"))
st.caption(ui_string("common.resolution_note"))

author = st.text_input(ui_string("author_activity.author_input"), value="")

col_from, col_to = st.columns(2)
from_dt = col_from.text_input(ui_string("common.from_ts"), value="")
to_dt = col_to.text_input(ui_string("common.to_ts"), value="")

if st.button(ui_string("author_activity.summarize"), disabled=not author):
    payload: dict[str, object] = {"author": author}
    if from_dt:
        payload["from"] = from_dt
    if to_dt:
        payload["to"] = to_dt
    try:
        result = client.call_tool("author_activity_summary", payload)
    except Exception as exc:
        st.error(ui_string("common.tool_call_failed").format(error=exc))
    else:
        summaries = result.get("summaries", [])
        st.write(ui_string("author_activity.matched").format(n=len(summaries)))
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
                st.info(ui_string("author_activity.no_topics"))
        if not summaries:
            st.info(ui_string("author_activity.no_author"))
