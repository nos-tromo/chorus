"""Natural-language agent chat UI.

Multi-turn: conversation history lives in ``st.session_state`` and is sent to
the stateless ``/agent/query`` endpoint on each turn. Each assistant reply
shows an expandable trace of the tools the agent called, for transparency.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import streamlit as st

from chorus.ui.client import ChorusClient

st.set_page_config(page_title="agent — chorus", layout="wide")


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


def _render_trace(trace: list[dict[str, Any]]) -> None:
    """Render the agent's tool-call trace inside an expander."""
    if not trace:
        return
    with st.expander(f"Tool calls ({len(trace)})"):
        for step in trace:
            tool = step.get("tool", "?")
            if step.get("error"):
                st.markdown(f"**{tool}** — error: {step['error']}")
            else:
                count = step.get("result_count")
                suffix = f" — {count} result(s)" if count is not None else ""
                st.markdown(f"**{tool}**{suffix}")
            st.json(step.get("arguments", {}))


client = _client()

st.title("chorus agent")
st.caption(
    "Ask in plain language; the agent picks the right tools. Topics cluster by "
    "canonical entity after a resolution pass; on un-resolved data they show as "
    "alias surface forms."
)

if "turns" not in st.session_state:
    st.session_state.turns = []

if st.button("Clear conversation"):
    st.session_state.turns = []

# Replay the conversation so far.
for turn in st.session_state.turns:
    with st.chat_message(turn["role"]):
        st.markdown(turn["content"])
        if turn["role"] == "assistant":
            _render_trace(turn.get("trace", []))

if prompt := st.chat_input("Ask a question about the network…"):
    st.session_state.turns.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    api_messages = [{"role": t["role"], "content": t["content"]} for t in st.session_state.turns]
    with st.chat_message("assistant"):
        try:
            with st.spinner("Thinking…"):
                result = client.agent_query(api_messages)
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("detail")
            except Exception:
                detail = None
            st.error(detail or f"agent call failed: {exc}")
        except Exception as exc:
            st.error(f"agent call failed: {exc}")
        else:
            answer = result.get("answer") or "(no answer)"
            if result.get("truncated"):
                st.warning("Stopped at the tool-call limit before reaching a final answer.")
            st.markdown(answer)
            trace = result.get("trace", [])
            _render_trace(trace)
            st.session_state.turns.append({"role": "assistant", "content": answer, "trace": trace})
