/**
 * Unified explorer node/edge model + pure mapper:
 * GraphState<ExplorerNode, ExplorerEdge> → ForceGraph props.
 *
 * No React, no rendering runtime — just converts the accumulated unified
 * explorer graph state (authors, topics, and `mentions`/`follows`/`friends`
 * edges) into the flat node/edge arrays the `@infra/ui` `ForceGraph`
 * primitive consumes. Dedup is `mergeGraph`'s job (graphExplorer.ts) via
 * `explorerEdgeKey`; the inputs here are already deduped, so this file
 * does none.
 */

import type { ForceGraphEdge, ForceGraphEdgeStyle, ForceGraphNode, ForceGraphNodeStyle } from '@infra/ui'
import type { GraphState } from './graphExplorer'

const SEED_SIZE_FLOOR = 6

export interface ExplorerNode {
  id: string // author:<id> | topic:<key>
  kind: 'author' | 'topic'
  label: string
  entity_id: string | null
  is_seed: boolean
}

export interface ExplorerEdge {
  source: string
  target: string
  kind: 'mentions' | 'follows' | 'friends'
  weight?: number
  directed?: boolean // true only for follows
}

/**
 * Node color palette, keyed by the `kind` value produced below.
 */
export const EXPLORER_NODE_STYLES: Record<string, ForceGraphNodeStyle> = {
  seed: { color: '#fbbf24' },
  author: { color: '#7c3aed', labelColor: '#a78bfa' },
  topic: { color: '#4ade80' },
}

/**
 * Edge styling, keyed by edge `kind`.
 */
export const EXPLORER_EDGE_STYLES: Record<string, ForceGraphEdgeStyle> = {
  mentions: { opacity: 0.6 },
  follows: { opacity: 0.7 },
  friends: { dashed: true },
}

/**
 * Dedup key for `mergeGraph` — includes `kind` so parallel edges of
 * different kinds between the same pair of nodes (e.g. a `mentions` edge
 * and a `follows` edge between the same two ids) both survive.
 */
export const explorerEdgeKey = (e: ExplorerEdge): string => `${e.source}__${e.target}__${e.kind}`

/**
 * Convert accumulated unified-explorer graph state into ForceGraph props.
 *
 * Node kind: `'seed'` when `is_seed`, else the backend `kind` verbatim
 * (`'author'` | `'topic'`).
 *
 * Node size:
 * - topic: `1 + total incident 'mentions' edge weight`; seed floor 6.
 * - author: `1 + total incident edge COUNT over all kinds` (weight
 *   ignored); seed floor 6.
 *
 * Edges pass through as `{source, target, kind, weight, directed}` — no
 * id, no dedup.
 */
export function toExplorerForceGraph(
  g: GraphState<ExplorerNode, ExplorerEdge>
): { nodes: ForceGraphNode[]; edges: ForceGraphEdge[] } {
  const mentionsWeight = new Map<string, number>()
  const incidentCount = new Map<string, number>()
  for (const edge of g.edges) {
    incidentCount.set(edge.source, (incidentCount.get(edge.source) ?? 0) + 1)
    incidentCount.set(edge.target, (incidentCount.get(edge.target) ?? 0) + 1)
    if (edge.kind === 'mentions') {
      const w = edge.weight ?? 0
      mentionsWeight.set(edge.source, (mentionsWeight.get(edge.source) ?? 0) + w)
      mentionsWeight.set(edge.target, (mentionsWeight.get(edge.target) ?? 0) + w)
    }
  }

  const nodes: ForceGraphNode[] = g.nodes.map((n) => {
    const kind = n.is_seed ? 'seed' : n.kind
    const raw =
      n.kind === 'topic'
        ? 1 + (mentionsWeight.get(n.id) ?? 0)
        : 1 + (incidentCount.get(n.id) ?? 0)
    return {
      id: n.id,
      label: n.label,
      kind,
      size: n.is_seed ? Math.max(raw, SEED_SIZE_FLOOR) : raw,
    }
  })

  const edges: ForceGraphEdge[] = g.edges.map((e) => ({
    source: e.source,
    target: e.target,
    kind: e.kind,
    weight: e.weight,
    directed: e.directed,
  }))

  return { nodes, edges }
}
