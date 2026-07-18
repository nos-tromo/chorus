/**
 * Pure mapper: GraphState<SocialNode, SocialEdge> → ForceGraph props.
 *
 * No React, no rendering runtime — just converts the accumulated ego-network
 * explorer state into the flat node/edge arrays the `@infra/ui` `ForceGraph`
 * primitive consumes. Dedup is `mergeGraph`'s job (graphExplorer.ts); the
 * inputs here are already deduped, so this file does none.
 */

import type {
  ForceGraphEdge,
  ForceGraphEdgeStyle,
  ForceGraphNode,
  ForceGraphNodeStyle,
} from '@infra/ui'
import type { GraphState } from './graphExplorer'
import type { SocialEdge, SocialNode } from '../api/types'

/**
 * Node color palette, keyed by the `kind` value produced below.
 */
export const SOCIAL_NODE_STYLES: Record<string, ForceGraphNodeStyle> = {
  seed: { color: '#fbbf24' },
  ring1: { color: '#7c3aed', labelColor: '#a78bfa' },
  ring2: { color: '#64748b', labelColor: '#94a3b8' },
  ringN: { color: '#b0bec5' },
}

/**
 * Edge styling, keyed by edge `kind`.
 */
export const SOCIAL_EDGE_STYLES: Record<string, ForceGraphEdgeStyle> = {
  follows: { opacity: 0.7 },
  friends: { dashed: true },
}

const RING_SIZE: Record<'seed' | 'ring1' | 'ring2' | 'ringN', number> = {
  seed: 6,
  ring1: 3,
  ring2: 2,
  ringN: 1,
}

function ringKind(node: SocialNode): 'seed' | 'ring1' | 'ring2' | 'ringN' {
  if (node.is_seed) return 'seed'
  if (node.ring === 1) return 'ring1'
  if (node.ring === 2) return 'ring2'
  return 'ringN'
}

/**
 * Convert accumulated social-explorer graph state into ForceGraph props.
 *
 * Node kind: `is_seed ? 'seed' : ring===1 ? 'ring1' : ring===2 ? 'ring2' : 'ringN'`.
 * Node size: seed 6, ring1 3, ring2 2, ringN 1.
 *
 * Edges pass through as `{source, target, kind, directed}` — `directed` is
 * true only for `follows` edges — no id, no dedup.
 */
export function toSocialForceGraph(
  g: GraphState<SocialNode, SocialEdge>
): { nodes: ForceGraphNode[]; edges: ForceGraphEdge[] } {
  const nodes: ForceGraphNode[] = g.nodes.map((n) => {
    const kind = ringKind(n)
    return {
      id: n.id,
      label: n.label,
      kind,
      size: RING_SIZE[kind],
    }
  })

  const edges: ForceGraphEdge[] = g.edges.map((e) => ({
    source: e.source,
    target: e.target,
    kind: e.kind,
    directed: e.kind === 'follows',
  }))

  return { nodes, edges }
}
