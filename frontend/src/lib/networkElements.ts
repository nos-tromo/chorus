/**
 * Pure mapper: GraphState<NetworkNode, NetworkEdge> → ForceGraph props.
 *
 * No React, no rendering runtime — just converts the accumulated explorer
 * graph state into the flat node/edge arrays the `@infra/ui` `ForceGraph`
 * primitive consumes. Dedup is `mergeGraph`'s job (graphExplorer.ts); the
 * inputs here are already deduped, so this file does none.
 */

import type { ForceGraphEdge, ForceGraphNode, ForceGraphNodeStyle } from '@infra/ui'
import type { GraphState } from './graphExplorer'
import type { NetworkEdge, NetworkNode } from '../api/types'

const SEED_SIZE_FLOOR = 6

/**
 * Node color palette, keyed by the `kind` value produced below.
 */
export const NETWORK_NODE_STYLES: Record<string, ForceGraphNodeStyle> = {
  seed: { color: '#fbbf24' },
  author: { color: '#7c3aed' },
  topic: { color: '#4ade80' },
}

/**
 * Convert accumulated network-explorer graph state into ForceGraph props.
 *
 * Node kind: `'seed'` when `is_seed`, else the backend `kind` verbatim
 * (`'author'` | `'topic'`).
 *
 * Node size: `1 + total incident edge weight` (sum of weights of edges
 * touching the node as source or target); seed nodes are floored at 6.
 *
 * Edges pass through as `{source, target, kind: 'mentions', weight}` —
 * no id, no dedup.
 */
export function toNetworkForceGraph(
  g: GraphState<NetworkNode, NetworkEdge>
): { nodes: ForceGraphNode[]; edges: ForceGraphEdge[] } {
  const incidentWeight = new Map<string, number>()
  for (const edge of g.edges) {
    incidentWeight.set(edge.source, (incidentWeight.get(edge.source) ?? 0) + edge.weight)
    incidentWeight.set(edge.target, (incidentWeight.get(edge.target) ?? 0) + edge.weight)
  }

  const nodes: ForceGraphNode[] = g.nodes.map((n) => {
    const size = 1 + (incidentWeight.get(n.id) ?? 0)
    return {
      id: n.id,
      label: n.label,
      kind: n.is_seed ? 'seed' : n.kind,
      size: n.is_seed ? Math.max(size, SEED_SIZE_FLOOR) : size,
    }
  })

  const edges: ForceGraphEdge[] = g.edges.map((e) => ({
    source: e.source,
    target: e.target,
    kind: 'mentions',
    weight: e.weight,
  }))

  return { nodes, edges }
}
