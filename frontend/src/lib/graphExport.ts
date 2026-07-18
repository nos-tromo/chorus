/**
 * Client-side export of the explorer graph — JSON and GraphML.
 *
 * Pure, no React, no network calls. Deliberately no backend endpoint and no
 * §76 audit row: the underlying data already arrived through audited tool
 * calls (`network_around`/`social_network_around` + the expand tools); this
 * module only serializes the state the client already holds. See the
 * "Graph export" addendum in `docs/decisions/0016-*.md`.
 *
 * Feed these directly with the `ForceGraph`-shaped `{nodes, edges}` produced
 * by `toNetworkForceGraph` / `toSocialForceGraph` — those mappers already
 * carry id/label/kind (nodes) and source/target/kind/weight/directed
 * (edges), so this module needs no per-screen knowledge of node/edge shape.
 */

import type { ForceGraphEdge, ForceGraphNode } from '@infra/ui'

/**
 * Pretty-printed (2-space) JSON passthrough of `{nodes, edges}`.
 */
export function toGraphJson(nodes: ForceGraphNode[], edges: ForceGraphEdge[]): string {
  return JSON.stringify({ nodes, edges }, null, 2)
}

const XML_ESCAPES: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}

function escapeXml(value: string): string {
  return value.replace(/[&<>"']/g, (ch) => XML_ESCAPES[ch])
}

/**
 * Serialize a `{nodes, edges}` graph as GraphML (Gephi/yEd-compatible).
 *
 * Declares `<key>` attrs for node `label`/`kind` (string) and edge `kind`
 * (string) / `weight` (double). `edgedefault` is `"undirected"`; individual
 * edges carry `directed="true"` when the source edge does. All attribute
 * and text values are XML-escaped since labels are user-derived data.
 */
export function toGraphML(nodes: ForceGraphNode[], edges: ForceGraphEdge[]): string {
  const hasWeight = edges.some((e) => e.weight !== undefined)

  const lines: string[] = []
  lines.push('<?xml version="1.0" encoding="UTF-8"?>')
  lines.push('<graphml xmlns="http://graphml.graphdrawing.org/xmlns">')
  lines.push('  <key id="d_n_label" for="node" attr.name="label" attr.type="string"/>')
  lines.push('  <key id="d_n_kind" for="node" attr.name="kind" attr.type="string"/>')
  lines.push('  <key id="d_e_kind" for="edge" attr.name="kind" attr.type="string"/>')
  if (hasWeight) {
    lines.push('  <key id="d_e_weight" for="edge" attr.name="weight" attr.type="double"/>')
  }
  lines.push('  <graph edgedefault="undirected">')

  for (const n of nodes) {
    lines.push(`    <node id="${escapeXml(n.id)}">`)
    lines.push(`      <data key="d_n_label">${escapeXml(n.label)}</data>`)
    lines.push(`      <data key="d_n_kind">${escapeXml(n.kind)}</data>`)
    lines.push('    </node>')
  }

  for (const e of edges) {
    const directedAttr = e.directed ? ' directed="true"' : ''
    lines.push(
      `    <edge source="${escapeXml(e.source)}" target="${escapeXml(e.target)}"${directedAttr}>`,
    )
    lines.push(`      <data key="d_e_kind">${escapeXml(e.kind)}</data>`)
    if (e.weight !== undefined) {
      lines.push(`      <data key="d_e_weight">${e.weight}</data>`)
    }
    lines.push('    </edge>')
  }

  lines.push('  </graph>')
  lines.push('</graphml>')
  return lines.join('\n')
}

/**
 * Trigger a client-side download of `text` as `filename` via a transient
 * Blob object URL — no backend round-trip.
 *
 * Defers URL.revokeObjectURL() via setTimeout(..., 0) to ensure the download
 * dispatch completes before the URL is revoked (older browsers may drop the
 * download if the URL is revoked too eagerly).
 */
// ── HTML export (interactive-lite) ─────────────────────────────────────────

const HTML_ESCAPES: Record<string, string> = XML_ESCAPES

function escapeHtml(value: string): string {
  return String(value).replace(/[&<>"']/g, (ch) => HTML_ESCAPES[ch])
}

/** Radius (px) for a node circle, sqrt-scaled from `size`, mirroring the app. */
function nodeRadius(size: number | undefined): number {
  return Math.min(34, 7 + Math.sqrt(Math.max(1, size ?? 1)) * 2.4)
}

interface Point {
  x: number
  y: number
}

/**
 * Resolve a position for every node: pass through baked `positions`, and lay
 * out anything missing on a small spiral below the bounding box of the
 * positions that *are* known (or the origin, if none are).
 */
function resolvePositions(
  nodes: ForceGraphNode[],
  positions: Record<string, Point>,
): Record<string, Point> {
  const resolved: Record<string, Point> = {}
  const missing: string[] = []

  for (const n of nodes) {
    const p = positions[n.id]
    if (p) {
      resolved[n.id] = p
    } else {
      missing.push(n.id)
    }
  }

  if (missing.length === 0) return resolved

  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const id in resolved) {
    const { x, y } = resolved[id]
    minX = Math.min(minX, x)
    minY = Math.min(minY, y)
    maxX = Math.max(maxX, x)
    maxY = Math.max(maxY, y)
  }
  if (!Number.isFinite(minX)) {
    minX = 0
    minY = 0
    maxX = 0
    maxY = 0
  }

  const cx = (minX + maxX) / 2
  const startY = maxY + 60
  missing.forEach((id, i) => {
    const angle = i * 2.4
    const spiralRadius = 20 + i * 14
    resolved[id] = {
      x: cx + spiralRadius * Math.cos(angle),
      y: startY + spiralRadius * Math.sin(angle),
    }
  })

  return resolved
}

function boundingBox(points: Point[]): { minX: number; minY: number; maxX: number; maxY: number } {
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const { x, y } of points) {
    minX = Math.min(minX, x)
    minY = Math.min(minY, y)
    maxX = Math.max(maxX, x)
    maxY = Math.max(maxY, y)
  }
  if (!Number.isFinite(minX)) {
    minX = 0
    minY = 0
    maxX = 0
    maxY = 0
  }
  return { minX, minY, maxX, maxY }
}

export interface GraphHtmlOptions {
  title: string
  nodes: ForceGraphNode[]
  edges: ForceGraphEdge[]
  positions: Record<string, Point>
  nodeStyles: Record<string, { color: string }>
  edgeStyles?: Record<string, { dashed?: boolean; opacity?: number }>
  legend?: { kind: string; label: string }[]
}

const EDGE_STROKE = '#71717a'
const LABEL_HALO = '#18181b'
const DEFAULT_EDGE_OPACITY = 0.6

/**
 * Render a single self-contained, "interactive-lite" HTML document: a dark
 * page embedding an inline SVG snapshot of the explorer graph with the
 * layout baked from `positions`, plus a small vanilla-JS pan/zoom script.
 *
 * No external requests of any kind — everything (CSS, JS, graph data) is
 * inlined. No physics, no fetch, no node expansion: this is a static export
 * of what the user was already looking at, not a live client. See the
 * "Graph export" addendum in `docs/decisions/0016-*.md` — same no-audit
 * posture as `toGraphJson` / `toGraphML`: the data was already audited when
 * the tool call that produced it ran.
 */
export function toGraphHtml(opts: GraphHtmlOptions): string {
  const { title, nodes, edges, positions, nodeStyles, edgeStyles = {}, legend = [] } = opts

  const resolved = resolvePositions(nodes, positions)
  const nodeById = new Map(nodes.map((n) => [n.id, n]))

  const PADDING = 40
  const { minX, minY, maxX, maxY } = boundingBox(Object.values(resolved))
  const viewMinX = minX - PADDING
  const viewMinY = minY - PADDING
  const viewWidth = Math.max(1, maxX - minX + PADDING * 2)
  const viewHeight = Math.max(1, maxY - minY + PADDING * 2)

  const edgeLines: string[] = []
  let hasDirected = false
  for (const e of edges) {
    const source = resolved[e.source]
    const target = resolved[e.target]
    if (!source || !target) continue
    const style = edgeStyles[e.kind]
    const opacity = style?.opacity ?? DEFAULT_EDGE_OPACITY
    const dashAttr = style?.dashed ? ' stroke-dasharray="6 4"' : ''

    let ex = target.x
    let ey = target.y
    let markerAttr = ''
    if (e.directed) {
      hasDirected = true
      const dx = target.x - source.x
      const dy = target.y - source.y
      const dist = Math.sqrt(dx * dx + dy * dy) || 1
      const targetNode = nodeById.get(e.target)
      const pullback = nodeRadius(targetNode?.size) + 4
      ex = target.x - (dx / dist) * pullback
      ey = target.y - (dy / dist) * pullback
      markerAttr = ' marker-end="url(#arrowhead)"'
    }

    edgeLines.push(
      `      <line x1="${source.x}" y1="${source.y}" x2="${ex}" y2="${ey}" ` +
        `stroke="${EDGE_STROKE}" stroke-opacity="${opacity}"${dashAttr}${markerAttr}>` +
        `<title>${escapeHtml(e.kind)}</title></line>`,
    )
  }

  const nodeCircles: string[] = []
  for (const n of nodes) {
    const pos = resolved[n.id]
    if (!pos) continue
    const color = nodeStyles[n.kind]?.color ?? '#94a3b8'
    const r = nodeRadius(n.size)
    const label = escapeHtml(n.label)
    nodeCircles.push(
      `      <g>` +
        `<circle cx="${pos.x}" cy="${pos.y}" r="${r}" fill="${color}">` +
        `<title>${label} (${escapeHtml(n.kind)})</title></circle>` +
        `<text x="${pos.x + r + 4}" y="${pos.y + 4}" fill="${color}" ` +
        `stroke="${LABEL_HALO}" stroke-width="3" paint-order="stroke" ` +
        `font-size="11" font-family="sans-serif">${label}</text>` +
        `</g>`,
    )
  }

  const legendChips = legend
    .map((entry) => {
      const color = nodeStyles[entry.kind]?.color ?? '#94a3b8'
      return (
        `<span style="display:inline-flex;align-items:center;gap:6px;margin-right:14px;">` +
        `<span style="width:10px;height:10px;border-radius:50%;background:${color};` +
        `display:inline-block;"></span>${escapeHtml(entry.label)}</span>`
      )
    })
    .join('')

  const markerDefs = hasDirected
    ? `    <defs>
      <marker id="arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z" fill="${EDGE_STROKE}" />
      </marker>
    </defs>\n`
    : ''

  // The `<script>` block below is a fully static template — no user-controlled
  // string is ever interpolated into it. The one exception is `VB`, the
  // viewBox baked as numeric literals: each component is coerced with
  // Number() and falls back to 0 if not finite, so only digits/minus/dot can
  // ever land in the emitted source (asserted in graphExport.test.ts).
  const vb = [viewMinX, viewMinY, viewWidth, viewHeight].map((n) => {
    const v = Number(n)
    return Number.isFinite(v) ? v : 0
  })
  const vbLiteral = `[${vb.join(', ')}]`

  const script = `<script>
  (function () {
    var svg = document.getElementById('graph-svg');
    var g = document.getElementById('graph-viewport');
    var VB = ${vbLiteral}; // [minX, minY, width, height] — baked numeric viewBox
    var scale = 1, tx = 0, ty = 0;

    function apply() {
      g.setAttribute('transform', 'translate(' + tx + ',' + ty + ') scale(' + scale + ')');
    }

    // preserveAspectRatio="xMidYMid meet" scales the viewBox to fit the
    // rendered (CSS pixel) box uniformly and centers it; convert pointer
    // coords from CSS pixels into viewBox units before using them, since tx/ty
    // translate a <g> living in viewBox space.
    function toViewBoxPoint(clientX, clientY) {
      var rect = svg.getBoundingClientRect();
      var s = Math.min(rect.width / VB[2], rect.height / VB[3]);
      var offX = (rect.width - VB[2] * s) / 2;
      var offY = (rect.height - VB[3] * s) / 2;
      return {
        x: VB[0] + (clientX - rect.left - offX) / s,
        y: VB[1] + (clientY - rect.top - offY) / s,
        s: s,
      };
    }

    svg.addEventListener('wheel', function (e) {
      e.preventDefault();
      var p = toViewBoxPoint(e.clientX, e.clientY);
      var prevScale = scale;
      var delta = e.deltaY < 0 ? 1.1 : 0.9;
      scale = Math.min(8, Math.max(0.15, scale * delta));
      tx = p.x - (p.x - tx) * (scale / prevScale);
      ty = p.y - (p.y - ty) * (scale / prevScale);
      apply();
    }, { passive: false });

    var dragging = false, lastX = 0, lastY = 0;
    svg.addEventListener('mousedown', function (e) {
      dragging = true;
      lastX = e.clientX;
      lastY = e.clientY;
    });
    window.addEventListener('mousemove', function (e) {
      if (!dragging) return;
      var rect = svg.getBoundingClientRect();
      var s = Math.min(rect.width / VB[2], rect.height / VB[3]);
      tx += (e.clientX - lastX) / s;
      ty += (e.clientY - lastY) / s;
      lastX = e.clientX;
      lastY = e.clientY;
      apply();
    });
    window.addEventListener('mouseup', function () {
      dragging = false;
    });
    svg.addEventListener('dblclick', function () {
      scale = 1;
      tx = 0;
      ty = 0;
      apply();
    });
  })();
</script>`

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>${escapeHtml(title)}</title>
<style>
  html, body { margin: 0; padding: 0; background: #0b0b0f; color: #e4e4e7; font-family: sans-serif; }
  h1 { font-size: 1.25rem; font-weight: 600; padding: 16px 20px 4px; margin: 0; }
  .counts { padding: 0 20px; color: #a1a1aa; font-size: 0.85rem; }
  .legend { padding: 8px 20px 16px; font-size: 0.85rem; }
  svg { width: 100%; height: calc(100vh - 110px); display: block; background: #0b0b0f; cursor: grab; }
</style>
</head>
<body>
<h1>${escapeHtml(title)}</h1>
<p class="counts">${nodes.length} nodes, ${edges.length} edges</p>
<p class="legend">${legendChips}</p>
<svg id="graph-svg" viewBox="${viewMinX} ${viewMinY} ${viewWidth} ${viewHeight}" preserveAspectRatio="xMidYMid meet">
${markerDefs}    <g id="graph-viewport">
${edgeLines.join('\n')}
${nodeCircles.join('\n')}
    </g>
</svg>
${script}
</body>
</html>
`
}

export function downloadText(filename: string, text: string, mimeType: string): void {
  const blob = new Blob([text], { type: mimeType })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}
