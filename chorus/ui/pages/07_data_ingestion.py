"""UI for client-driven data ingestion — upload, migrate, ingest, resolve.

This is the frontend counterpart to ADR 0014: instead of dropping CSVs on the
server and running ``make ingest``, an analyst uploads table dumps from their
own machine and runs the pipeline end-to-end here. The page is gated by the
backend ``INGESTION_UI_ENABLED`` flag (queried on load) and self-disables when
it is off. Ingest and resolve run as background jobs that this page polls.
"""

from __future__ import annotations

import time

import httpx
import streamlit as st

from chorus.ui.client import ChorusClient
from chorus.utils.ui_strings import ui_string

st.set_page_config(page_title="data ingestion — chorus")


@st.cache_resource
def _client() -> ChorusClient:
    """Construct (or return the cached) :class:`ChorusClient` for this page.

    Returns:
        A :class:`ChorusClient` bound to the configured API URL and identity,
        both pulled from the environment with development defaults
        (``http://localhost:8000`` and ``"dev"``).
    """
    return ChorusClient.from_env()


def _detail(exc: Exception) -> str:
    """Best-effort human-readable detail from an HTTP error (or its string)."""
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            payload = exc.response.json()
        except Exception:
            return exc.response.text or str(exc)
        detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
        return str(detail)
    return str(exc)


def _render_resolution(res: dict[str, object]) -> None:
    """Render a resolution summary dict as a labeled table."""
    st.caption(ui_string("ingest.resolve.summary"))
    st.table([{"metric": k, "count": v} for k, v in res.items()])


def _nonzero(value: object) -> dict[str, object]:
    """Return the truthy entries of a count dict, or empty if not a dict."""
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if v}


def _render_ingest_result(result: dict[str, object]) -> None:
    """Render an ingest job's counts and any dropped/filtered/skipped detail."""
    counts = result.get("counts")
    if isinstance(counts, dict):
        st.caption(ui_string("ingest.counts.header"))
        st.table([{"stage": k, "count": v} for k, v in counts.items()])

    dropped = _nonzero(result.get("dropped"))
    if dropped:
        st.warning(ui_string("ingest.counts.dropped").format(detail=dropped))
    filtered = _nonzero(result.get("filtered"))
    if filtered:
        st.info(ui_string("ingest.counts.filtered").format(detail=filtered))
    skipped = result.get("skipped")
    if isinstance(skipped, list) and skipped:
        st.info(ui_string("ingest.counts.skipped").format(detail=skipped))

    resolution = result.get("resolution")
    if isinstance(resolution, dict):
        _render_resolution(resolution)
    if "resolution_error" in result:
        st.warning(ui_string("ingest.resolve.failed").format(error=result["resolution_error"]))


def _poll_job(client: ChorusClient, state_key: str, running_msg: str, failed_msg: str) -> dict[str, object] | None:
    """Poll the job stored under ``state_key``; rerun while in flight.

    Returns the job's ``result`` dict once done, ``None`` while running or on
    error (after surfacing it). Clears the session key on a terminal state.
    """
    job_id = st.session_state.get(state_key)
    if not job_id:
        return None
    try:
        job = client.job_status(job_id)
    except Exception as exc:
        st.error(ui_string("common.tool_call_failed").format(error=_detail(exc)))
        st.session_state.pop(state_key, None)
        return None

    status = job.get("status")
    if status in ("queued", "running"):
        st.info(running_msg)
        time.sleep(1.5)
        st.rerun()
    if status == "error":
        st.error(failed_msg.format(error=job.get("error")))
        st.session_state.pop(state_key, None)
        return None
    # done
    st.session_state.pop(state_key, None)
    result = job.get("result") or {}
    return result if isinstance(result, dict) else {}


client = _client()
st.title(ui_string("ingest.title"))

# --- Feature gate: ask the backend on load (single source of truth) ---------
try:
    enabled = bool(client.ingestion_status().get("enabled", False))
except Exception as exc:
    st.error(ui_string("common.unreachable").format(error=_detail(exc)))
    st.stop()
if not enabled:
    st.info(ui_string("ingest.disabled"))
    st.stop()

st.caption(ui_string("ingest.caption"))

_ingest_active = "ingest_job_id" in st.session_state
_resolve_active = "resolve_job_id" in st.session_state
_busy = _ingest_active or _resolve_active

# --- Migrations -------------------------------------------------------------
st.subheader(ui_string("ingest.migrations.header"))
try:
    mig = client.migrations()
    if mig.get("pending"):
        st.warning(ui_string("ingest.migrations.pending").format(versions=mig["pending"]))
        if st.button(ui_string("ingest.migrations.apply"), disabled=_busy):
            with st.spinner(ui_string("ingest.migrations.applying")):
                applied = client.migrate().get("applied", [])
            st.success(ui_string("ingest.migrations.applied").format(versions=applied))
            st.rerun()
    else:
        st.success(ui_string("ingest.migrations.uptodate"))
except Exception as exc:
    st.error(ui_string("common.tool_call_failed").format(error=_detail(exc)))

# --- Ingest -----------------------------------------------------------------
st.subheader(ui_string("ingest.upload.header"))
st.caption(ui_string("ingest.upload.help"))
uploaded = st.file_uploader(ui_string("ingest.upload.label"), accept_multiple_files=True, type=["csv"])
since = st.text_input(ui_string("ingest.upload.since"), value="")
then_resolve = st.checkbox(ui_string("ingest.upload.then_resolve"), value=False)

if st.button(ui_string("ingest.upload.start"), disabled=_busy or not uploaded):
    files = [(f.name, f.getvalue()) for f in uploaded]
    try:
        accepted = client.ingest(files, since=since or None, then_resolve=then_resolve)
    except Exception as exc:
        st.error(ui_string("ingest.error.request").format(detail=_detail(exc)))
    else:
        st.session_state["ingest_job_id"] = accepted["job_id"]
        st.rerun()

if _ingest_active:
    result = _poll_job(
        client,
        "ingest_job_id",
        ui_string("ingest.job.running"),
        ui_string("ingest.job.failed"),
    )
    if result is not None:
        st.success(ui_string("ingest.job.done"))
        _render_ingest_result(result)

# --- Resolve ----------------------------------------------------------------
st.subheader(ui_string("ingest.resolve.header"))
if st.button(ui_string("ingest.resolve.start"), disabled=_busy):
    try:
        accepted = client.resolve()
    except Exception as exc:
        st.error(ui_string("ingest.error.request").format(detail=_detail(exc)))
    else:
        st.session_state["resolve_job_id"] = accepted["job_id"]
        st.rerun()

if _resolve_active:
    result = _poll_job(
        client,
        "resolve_job_id",
        ui_string("ingest.resolve.running"),
        ui_string("ingest.resolve.failed"),
    )
    if result is not None:
        st.success(ui_string("ingest.resolve.done"))
        resolution = result.get("resolution")
        if isinstance(resolution, dict):
            _render_resolution(resolution)
