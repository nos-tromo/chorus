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
 */
export function downloadText(filename: string, text: string, mimeType: string): void {
  const blob = new Blob([text], { type: mimeType })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}
