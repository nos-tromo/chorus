/**
 * Pure mapper: SocialNetworkAroundOut → Cytoscape ElementDefinition[].
 *
 * No React, no Cytoscape runtime — just converts the backend's node/edge
 * lists into the element array the Cytoscape component expects.
 *
 * Styling (colors, shapes) is the stylesheet's responsibility (Task 18).
 * This file owns only the semantic class names and data fields.
 */

import type { ElementDefinition } from 'cytoscape'
import type { SocialNetworkAroundOut } from '../api/types'

/**
 * Map a ring number (and the is_seed override) to a CSS class string,
 * preserving the same visual grouping as the DOT renderer:
 *
 *   is_seed OR ring === 0  →  'seed'
 *   ring === 1             →  'ring1'
 *   ring === 2             →  'ring2'
 *   ring >= 3              →  'ringN'
 */
function ringClass(ring: number, isSeed: boolean): string {
  if (isSeed || ring === 0) return 'seed'
  if (ring === 1) return 'ring1'
  if (ring === 2) return 'ring2'
  return 'ringN'
}

/**
 * Convert a SocialNetworkAroundOut (from the social_network_around tool) into
 * a flat array of Cytoscape ElementDefinition objects.
 *
 * Node classes (single-class string derived from ring/is_seed):
 *   'seed'  | 'ring1'  | 'ring2'  | 'ringN'
 *
 * Edge classes: the kind value verbatim — 'follows' or 'friends'.
 *
 * Edge data:
 *   - id       = `${source}__${target}`  (stable, dedupe key)
 *   - source, target, kind, directed     (pass-through)
 *
 * Duplicate edges (same source+target pair) are silently deduplicated —
 * the first occurrence wins.
 */
export function toSocialElements(out: SocialNetworkAroundOut): ElementDefinition[] {
  const elements: ElementDefinition[] = []

  for (const node of out.nodes) {
    elements.push({
      data: {
        id: node.id,
        label: node.label,
        ring: node.ring,
        isSeed: node.is_seed,
      },
      classes: ringClass(node.ring, node.is_seed),
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
        kind: edge.kind,
        directed: edge.directed,
      },
      classes: edge.kind,
    })
  }

  return elements
}
