/**
 * Pure mapper: NetworkAroundOut → Cytoscape ElementDefinition[].
 *
 * No React, no Cytoscape runtime — just converts the backend's node/edge
 * lists into the element array the Cytoscape component expects.
 *
 * Styling (colors, shapes) is the stylesheet's responsibility (Task 18).
 * This file owns only the semantic class names and computed data fields.
 */

import type { ElementDefinition } from 'cytoscape'
import type { NetworkAroundOut } from '../api/types'

const MAX_WIDTH = 6.0

/**
 * Map a backend edge weight to a line width using the same formula as the
 * DOT renderer: 1.0 + 0.5 * max(weight - 1, 0), capped at MAX_WIDTH.
 */
function penwidth(weight: number): number {
  return Math.min(1.0 + 0.5 * Math.max(weight - 1, 0), MAX_WIDTH)
}

/**
 * Convert a NetworkAroundOut (from the network_around tool) into a flat
 * array of Cytoscape ElementDefinition objects suitable for passing to
 * cytoscape({ elements }).
 *
 * Node classes (space-joined string):
 *   - 'author'  when kind === 'author'
 *   - 'topic'   when kind === 'topic'
 *   - 'seed'    additionally when is_seed === true
 *
 * Edge data:
 *   - id      = `${source}__${target}`  (stable, dedupe key)
 *   - source, target, weight            (pass-through)
 *   - width                             (computed penwidth)
 *
 * Duplicate edges (same source+target pair) are silently deduplicated —
 * the first occurrence wins.
 */
export function toNetworkElements(out: NetworkAroundOut): ElementDefinition[] {
  const elements: ElementDefinition[] = []

  for (const node of out.nodes) {
    const classParts: string[] = [node.kind]
    if (node.is_seed) classParts.push('seed')

    elements.push({
      data: {
        id: node.id,
        label: node.label,
        kind: node.kind,
        isSeed: node.is_seed,
        entityId: node.entity_id,
      },
      classes: classParts.join(' '),
    })
  }

  const seenEdgeIds = new Set<string>()
  for (const edge of out.edges) {
    const id = `${edge.source}__${edge.target}`
    if (seenEdgeIds.has(id)) continue
    seenEdgeIds.add(id)

    elements.push({
      data: {
        id,
        source: edge.source,
        target: edge.target,
        weight: edge.weight,
        width: penwidth(edge.weight),
      },
    })
  }

  return elements
}
