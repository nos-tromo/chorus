# airgap notes

Disabled telemetry knobs and non-obvious offline-readiness findings accumulate
here as dependencies are added. See *Airgapped operation* in
[`CLAUDE.md`](../CLAUDE.md) for the hard rules.

## Streamlit UI

**Telemetry is off.** `docker/Dockerfile.frontend` sets
`STREAMLIT_BROWSER_GATHER_USAGE_STATS=false` (and `STREAMLIT_SERVER_HEADLESS=true`),
so the frontend never calls Streamlit's `data.streamlit.io` metrics endpoints. The
other `streamlit.io` / `docs.streamlit.io` URLs in the bundle are hamburger-menu
links — fetched only on user click, never automatically.

**Graph rendering needs no `graphviz` binary and makes no network call**
(verified 2026-06-05, Streamlit 1.58.0, for the `network_around` page). The
render path is `chorus/ui/network_dot.py:to_dot` → `st.graphviz_chart(<dot string>)`
in `chorus/ui/pages/06_network_around.py`. chorus builds the DOT string by hand
rather than passing a `graphviz.Digraph` object, which keeps the whole path off
the Python `graphviz` package: it is **not** a dependency, and there is no system
`dot` binary in the image. Streamlit's `graphviz_chart` marshals a `str` input
straight into the proto (`engine="dot"` is just the layout-engine name, not a
subprocess) and the browser renders it client-side with the **dagre-d3** bundle
shipped in Streamlit's own static assets (`static/js/GraphVizChart.*.js`) — no
wasm, no CDN. A live browser capture of the page (all requests, static included)
showed every request hitting `127.0.0.1` only; even the UI font is served locally
from `/static/media/`, so the `fonts.gstatic.com` string present in the bundle is
never fetched.

Reproduce: render any `network_around` result through `st.graphviz_chart` with a
headless Streamlit server and confirm the browser issues zero non-localhost
requests while the SVG draws.
