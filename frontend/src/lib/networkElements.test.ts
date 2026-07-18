import { describe, it, expect } from 'vitest'
import { NETWORK_NODE_STYLES, toNetworkForceGraph } from './networkElements'
import type { GraphState } from './graphExplorer'
import type { NetworkNode, NetworkEdge } from '../api/types'

function makeState(
  nodes: NetworkNode[] = [],
  edges: NetworkEdge[] = []
): GraphState<NetworkNode, NetworkEdge> {
  return { nodes, edges }
}

describe('NETWORK_NODE_STYLES', () => {
  it('defines exactly seed, author, topic with the fixed palette', () => {
    expect(NETWORK_NODE_STYLES).toEqual({
      seed: { color: '#fbbf24' },
      author: { color: '#7c3aed' },
      topic: { color: '#4ade80' },
    })
  })
})

describe('toNetworkForceGraph — nodes', () => {
  it('returns empty nodes/edges for an empty state', () => {
    expect(toNetworkForceGraph(makeState())).toEqual({ nodes: [], edges: [] })
  })

  it('non-seed node kind passes through the backend kind (author)', () => {
    const state = makeState([
      { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: false },
    ])
    const { nodes } = toNetworkForceGraph(state)
    expect(nodes[0].kind).toBe('author')
  })

  it('non-seed node kind passes through the backend kind (topic)', () => {
    const state = makeState([
      { id: 'topic:t', kind: 'topic', label: 'T', entity_id: 'eid-1', is_seed: false },
    ])
    const { nodes } = toNetworkForceGraph(state)
    expect(nodes[0].kind).toBe('topic')
  })

  it('is_seed=true overrides kind to "seed" regardless of backend kind', () => {
    const state = makeState([
      { id: 'author:seed', kind: 'author', label: 'Seed', entity_id: null, is_seed: true },
    ])
    const { nodes } = toNetworkForceGraph(state)
    expect(nodes[0].kind).toBe('seed')
  })

  it('node size is 1 + total incident edge weight', () => {
    const state = makeState(
      [
        { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: false },
        { id: 'topic:t', kind: 'topic', label: 'T', entity_id: null, is_seed: false },
        { id: 'topic:u', kind: 'topic', label: 'U', entity_id: null, is_seed: false },
      ],
      [
        { source: 'author:a', target: 'topic:t', weight: 2 },
        { source: 'author:a', target: 'topic:u', weight: 3 },
      ]
    )
    const { nodes } = toNetworkForceGraph(state)
    const a = nodes.find((n) => n.id === 'author:a')!
    expect(a.size).toBe(1 + 2 + 3)
  })

  it('node with no incident edges has size 1', () => {
    const state = makeState([
      { id: 'author:lonely', kind: 'author', label: 'Lonely', entity_id: null, is_seed: false },
    ])
    const { nodes } = toNetworkForceGraph(state)
    expect(nodes[0].size).toBe(1)
  })

  it('seed node has a floor of 6 even with low incident weight', () => {
    const state = makeState(
      [
        { id: 'author:seed', kind: 'author', label: 'Seed', entity_id: null, is_seed: true },
        { id: 'topic:t', kind: 'topic', label: 'T', entity_id: null, is_seed: false },
      ],
      [{ source: 'author:seed', target: 'topic:t', weight: 1 }]
    )
    const { nodes } = toNetworkForceGraph(state)
    const seed = nodes.find((n) => n.id === 'author:seed')!
    expect(seed.size).toBe(6)
  })

  it('seed node size exceeds the floor when incident weight warrants it', () => {
    const state = makeState(
      [
        { id: 'author:seed', kind: 'author', label: 'Seed', entity_id: null, is_seed: true },
        { id: 'topic:t', kind: 'topic', label: 'T', entity_id: null, is_seed: false },
      ],
      [{ source: 'author:seed', target: 'topic:t', weight: 20 }]
    )
    const { nodes } = toNetworkForceGraph(state)
    const seed = nodes.find((n) => n.id === 'author:seed')!
    expect(seed.size).toBe(21)
  })
})

describe('toNetworkForceGraph — edges', () => {
  it('edge shape is {source, target, kind: "mentions", weight}', () => {
    const state = makeState(
      [
        { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: false },
        { id: 'topic:t', kind: 'topic', label: 'T', entity_id: null, is_seed: false },
      ],
      [{ source: 'author:a', target: 'topic:t', weight: 5 }]
    )
    const { edges } = toNetworkForceGraph(state)
    expect(edges).toEqual([{ source: 'author:a', target: 'topic:t', kind: 'mentions', weight: 5 }])
  })

  it('no dedup — duplicate input edges both pass through', () => {
    const state = makeState(
      [
        { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: false },
        { id: 'topic:t', kind: 'topic', label: 'T', entity_id: null, is_seed: false },
      ],
      [
        { source: 'author:a', target: 'topic:t', weight: 2 },
        { source: 'author:a', target: 'topic:t', weight: 3 },
      ]
    )
    const { edges } = toNetworkForceGraph(state)
    expect(edges).toHaveLength(2)
  })
})

describe('toNetworkForceGraph — determinism', () => {
  it('same input produces deep-equal output across calls', () => {
    const state = makeState(
      [
        { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: true },
        { id: 'topic:t', kind: 'topic', label: 'T', entity_id: null, is_seed: false },
      ],
      [{ source: 'author:a', target: 'topic:t', weight: 4 }]
    )
    expect(toNetworkForceGraph(state)).toEqual(toNetworkForceGraph(state))
  })
})
