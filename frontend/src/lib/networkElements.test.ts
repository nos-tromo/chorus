import { describe, it, expect } from 'vitest'
import { toNetworkElements } from './networkElements'
import type { NetworkAroundOut } from '../api/types'

// Helpers
function makeOut(overrides: Partial<NetworkAroundOut> = {}): NetworkAroundOut {
  return {
    seed: 'TestEntity',
    seed_node_id: 'topic:testtopic',
    nodes: [],
    edges: [],
    truncated: false,
    ...overrides,
  }
}

describe('toNetworkElements — nodes', () => {
  it('returns an empty array for an empty network', () => {
    const elements = toNetworkElements(makeOut())
    expect(elements).toEqual([])
  })

  it('seed topic node gets classes containing "seed" and "topic"', () => {
    const out = makeOut({
      nodes: [{ id: 'topic:x', kind: 'topic', label: 'X', entity_id: null, is_seed: true }],
    })
    const elements = toNetworkElements(out)
    const node = elements.find((e) => e.data.id === 'topic:x')
    expect(node).toBeDefined()
    const classes = (node!.classes as string).split(' ')
    expect(classes).toContain('seed')
    expect(classes).toContain('topic')
  })

  it('non-seed topic node has class "topic" but NOT "seed"', () => {
    const out = makeOut({
      nodes: [{ id: 'topic:y', kind: 'topic', label: 'Y', entity_id: 'eid-1', is_seed: false }],
    })
    const elements = toNetworkElements(out)
    const node = elements[0]
    const classes = (node.classes as string).split(' ')
    expect(classes).toContain('topic')
    expect(classes).not.toContain('seed')
  })

  it('author node has class "author" and carries isSeed=false when not seed', () => {
    const out = makeOut({
      nodes: [{ id: 'author:alice', kind: 'author', label: 'alice', entity_id: null, is_seed: false }],
    })
    const elements = toNetworkElements(out)
    const node = elements[0]
    const classes = (node.classes as string).split(' ')
    expect(classes).toContain('author')
    expect(classes).not.toContain('seed')
    expect(node.data.isSeed).toBe(false)
  })

  it('is_seed=true on an author node also adds "seed" class', () => {
    // Unusual but the mapping must honour the flag regardless of kind
    const out = makeOut({
      nodes: [{ id: 'author:bob', kind: 'author', label: 'bob', entity_id: null, is_seed: true }],
    })
    const elements = toNetworkElements(out)
    const node = elements[0]
    const classes = (node.classes as string).split(' ')
    expect(classes).toContain('seed')
    expect(classes).toContain('author')
  })

  it('node data carries id, label, kind, isSeed, entityId', () => {
    const out = makeOut({
      nodes: [{ id: 'topic:z', kind: 'topic', label: 'Zeta', entity_id: 'eid-z', is_seed: false }],
    })
    const node = toNetworkElements(out)[0]
    expect(node.data.id).toBe('topic:z')
    expect(node.data.label).toBe('Zeta')
    expect(node.data.kind).toBe('topic')
    expect(node.data.isSeed).toBe(false)
    expect(node.data.entityId).toBe('eid-z')
  })
})

describe('toNetworkElements — edges', () => {
  it('edge id is "${source}__${target}"', () => {
    const out = makeOut({
      nodes: [
        { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: false },
        { id: 'topic:t', kind: 'topic', label: 'T', entity_id: null, is_seed: false },
      ],
      edges: [{ source: 'author:a', target: 'topic:t', weight: 1 }],
    })
    const elements = toNetworkElements(out)
    const edge = elements.find((e) => !('kind' in (e.data as object)) && e.data.source)
    expect(edge!.data.id).toBe('author:a__topic:t')
  })

  it('duplicate edges (same source+target) are deduped to one', () => {
    const out = makeOut({
      nodes: [
        { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: false },
        { id: 'topic:t', kind: 'topic', label: 'T', entity_id: null, is_seed: false },
      ],
      edges: [
        { source: 'author:a', target: 'topic:t', weight: 2 },
        { source: 'author:a', target: 'topic:t', weight: 3 },
      ],
    })
    const elements = toNetworkElements(out)
    const edgeElements = elements.filter((e) => e.data.source)
    expect(edgeElements).toHaveLength(1)
  })

  it('weight 1 → width 1.0 (minimum)', () => {
    const out = makeOut({
      nodes: [
        { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: false },
        { id: 'topic:t', kind: 'topic', label: 'T', entity_id: null, is_seed: false },
      ],
      edges: [{ source: 'author:a', target: 'topic:t', weight: 1 }],
    })
    const elements = toNetworkElements(out)
    const edge = elements.find((e) => e.data.source)!
    expect(edge.data.width).toBeCloseTo(1.0)
  })

  it('weight 3 → width 2.0', () => {
    const out = makeOut({
      nodes: [
        { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: false },
        { id: 'topic:t', kind: 'topic', label: 'T', entity_id: null, is_seed: false },
      ],
      edges: [{ source: 'author:a', target: 'topic:t', weight: 3 }],
    })
    const elements = toNetworkElements(out)
    const edge = elements.find((e) => e.data.source)!
    expect(edge.data.width).toBeCloseTo(2.0)
  })

  it('large weight caps at 6.0', () => {
    const out = makeOut({
      nodes: [
        { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: false },
        { id: 'topic:t', kind: 'topic', label: 'T', entity_id: null, is_seed: false },
      ],
      edges: [{ source: 'author:a', target: 'topic:t', weight: 999 }],
    })
    const elements = toNetworkElements(out)
    const edge = elements.find((e) => e.data.source)!
    expect(edge.data.width).toBeCloseTo(6.0)
  })

  it('weight and source/target are preserved on edge data', () => {
    const out = makeOut({
      nodes: [
        { id: 'author:x', kind: 'author', label: 'X', entity_id: null, is_seed: false },
        { id: 'topic:y', kind: 'topic', label: 'Y', entity_id: null, is_seed: false },
      ],
      edges: [{ source: 'author:x', target: 'topic:y', weight: 5 }],
    })
    const elements = toNetworkElements(out)
    const edge = elements.find((e) => e.data.source)!
    expect(edge.data.source).toBe('author:x')
    expect(edge.data.target).toBe('topic:y')
    expect(edge.data.weight).toBe(5)
  })
})
