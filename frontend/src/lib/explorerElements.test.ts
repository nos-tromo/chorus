import { describe, it, expect } from 'vitest'
import {
  EXPLORER_NODE_STYLES,
  EXPLORER_EDGE_STYLES,
  explorerEdgeKey,
  toExplorerForceGraph,
} from './explorerElements'
import type { GraphState } from './graphExplorer'
import type { ExplorerNode, ExplorerEdge } from './explorerElements'

function makeState(
  nodes: ExplorerNode[] = [],
  edges: ExplorerEdge[] = []
): GraphState<ExplorerNode, ExplorerEdge> {
  return { nodes, edges }
}

describe('EXPLORER_NODE_STYLES / EXPLORER_EDGE_STYLES', () => {
  it('defines exactly seed, author, topic with the fixed palette', () => {
    expect(EXPLORER_NODE_STYLES).toEqual({
      seed: { color: '#fbbf24' },
      author: { color: '#7c3aed', labelColor: '#a78bfa' },
      topic: { color: '#4ade80' },
    })
  })

  it('defines mentions, follows opacity and friends dashed', () => {
    expect(EXPLORER_EDGE_STYLES).toEqual({
      mentions: { opacity: 0.6 },
      follows: { opacity: 0.7 },
      friends: { dashed: true },
    })
  })
})

describe('explorerEdgeKey', () => {
  it('combines source, target, and kind', () => {
    const edge: ExplorerEdge = { source: 'author:a', target: 'topic:t', kind: 'mentions' }
    expect(explorerEdgeKey(edge)).toBe('author:a__topic:t__mentions')
  })

  it('distinguishes edges that share source/target but differ in kind', () => {
    const mentions: ExplorerEdge = { source: 'author:a', target: 'author:b', kind: 'mentions' }
    const follows: ExplorerEdge = { source: 'author:a', target: 'author:b', kind: 'follows' }
    expect(explorerEdgeKey(mentions)).not.toBe(explorerEdgeKey(follows))
  })
})

describe('toExplorerForceGraph — nodes', () => {
  it('returns empty nodes/edges for an empty state', () => {
    expect(toExplorerForceGraph(makeState())).toEqual({ nodes: [], edges: [] })
  })

  it('non-seed node kind passes through the backend kind (author)', () => {
    const state = makeState([
      { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: false },
    ])
    const { nodes } = toExplorerForceGraph(state)
    expect(nodes[0].kind).toBe('author')
  })

  it('non-seed node kind passes through the backend kind (topic)', () => {
    const state = makeState([
      { id: 'topic:t', kind: 'topic', label: 'T', entity_id: 'eid-1', is_seed: false },
    ])
    const { nodes } = toExplorerForceGraph(state)
    expect(nodes[0].kind).toBe('topic')
  })

  it('is_seed=true overrides kind to "seed" regardless of backend kind', () => {
    const state = makeState([
      { id: 'author:seed', kind: 'author', label: 'Seed', entity_id: null, is_seed: true },
    ])
    const { nodes } = toExplorerForceGraph(state)
    expect(nodes[0].kind).toBe('seed')
  })

  it('topic size is 1 + sum of incident mentions weight', () => {
    const state = makeState(
      [
        { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: false },
        { id: 'topic:t', kind: 'topic', label: 'T', entity_id: null, is_seed: false },
      ],
      [
        { source: 'author:a', target: 'topic:t', kind: 'mentions', weight: 2 },
        { source: 'author:a', target: 'topic:t', kind: 'mentions', weight: 3 },
      ]
    )
    const { nodes } = toExplorerForceGraph(state)
    const topic = nodes.find((n) => n.id === 'topic:t')!
    expect(topic.size).toBe(1 + 2 + 3)
  })

  it('topic node with no incident edges has size 1', () => {
    const state = makeState([
      { id: 'topic:lonely', kind: 'topic', label: 'Lonely', entity_id: null, is_seed: false },
    ])
    const { nodes } = toExplorerForceGraph(state)
    expect(nodes[0].size).toBe(1)
  })

  it('seed topic has a floor of 6 even with low incident mentions weight', () => {
    const state = makeState(
      [
        { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: false },
        { id: 'topic:seed', kind: 'topic', label: 'Seed', entity_id: null, is_seed: true },
      ],
      [{ source: 'author:a', target: 'topic:seed', kind: 'mentions', weight: 1 }]
    )
    const { nodes } = toExplorerForceGraph(state)
    const seed = nodes.find((n) => n.id === 'topic:seed')!
    expect(seed.size).toBe(6)
  })

  it('seed topic size exceeds the floor when incident mentions weight warrants it', () => {
    const state = makeState(
      [
        { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: false },
        { id: 'topic:seed', kind: 'topic', label: 'Seed', entity_id: null, is_seed: true },
      ],
      [{ source: 'author:a', target: 'topic:seed', kind: 'mentions', weight: 20 }]
    )
    const { nodes } = toExplorerForceGraph(state)
    const seed = nodes.find((n) => n.id === 'topic:seed')!
    expect(seed.size).toBe(21)
  })

  it('author size is 1 + incident edge COUNT over all kinds, ignoring weight', () => {
    const state = makeState(
      [
        { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: false },
        { id: 'author:b', kind: 'author', label: 'B', entity_id: null, is_seed: false },
        { id: 'topic:t', kind: 'topic', label: 'T', entity_id: null, is_seed: false },
      ],
      [
        { source: 'author:a', target: 'topic:t', kind: 'mentions', weight: 10 },
        { source: 'author:a', target: 'author:b', kind: 'follows', directed: true },
        { source: 'author:a', target: 'author:b', kind: 'friends' },
      ]
    )
    const { nodes } = toExplorerForceGraph(state)
    const a = nodes.find((n) => n.id === 'author:a')!
    expect(a.size).toBe(1 + 3)
  })

  it('author with no incident edges has size 1', () => {
    const state = makeState([
      { id: 'author:lonely', kind: 'author', label: 'Lonely', entity_id: null, is_seed: false },
    ])
    const { nodes } = toExplorerForceGraph(state)
    expect(nodes[0].size).toBe(1)
  })

  it('seed author has a floor of 6 even with a low incident edge count', () => {
    const state = makeState(
      [
        { id: 'author:seed', kind: 'author', label: 'Seed', entity_id: null, is_seed: true },
        { id: 'author:b', kind: 'author', label: 'B', entity_id: null, is_seed: false },
      ],
      [{ source: 'author:seed', target: 'author:b', kind: 'follows', directed: true }]
    )
    const { nodes } = toExplorerForceGraph(state)
    const seed = nodes.find((n) => n.id === 'author:seed')!
    expect(seed.size).toBe(6)
  })

  it('seed author size exceeds the floor when incident edge count warrants it', () => {
    const state = makeState(
      [
        { id: 'author:seed', kind: 'author', label: 'Seed', entity_id: null, is_seed: true },
        { id: 'author:b', kind: 'author', label: 'B', entity_id: null, is_seed: false },
        { id: 'author:c', kind: 'author', label: 'C', entity_id: null, is_seed: false },
        { id: 'author:d', kind: 'author', label: 'D', entity_id: null, is_seed: false },
        { id: 'author:e', kind: 'author', label: 'E', entity_id: null, is_seed: false },
        { id: 'author:f', kind: 'author', label: 'F', entity_id: null, is_seed: false },
        { id: 'author:g', kind: 'author', label: 'G', entity_id: null, is_seed: false },
      ],
      [
        { source: 'author:seed', target: 'author:b', kind: 'follows', directed: true },
        { source: 'author:seed', target: 'author:c', kind: 'follows', directed: true },
        { source: 'author:seed', target: 'author:d', kind: 'follows', directed: true },
        { source: 'author:seed', target: 'author:e', kind: 'follows', directed: true },
        { source: 'author:seed', target: 'author:f', kind: 'follows', directed: true },
        { source: 'author:seed', target: 'author:g', kind: 'follows', directed: true },
      ]
    )
    const { nodes } = toExplorerForceGraph(state)
    const seed = nodes.find((n) => n.id === 'author:seed')!
    expect(seed.size).toBe(7)
  })
})

describe('toExplorerForceGraph — edges', () => {
  it('mentions edge passes through as {source, target, kind, weight}', () => {
    const state = makeState(
      [
        { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: false },
        { id: 'topic:t', kind: 'topic', label: 'T', entity_id: null, is_seed: false },
      ],
      [{ source: 'author:a', target: 'topic:t', kind: 'mentions', weight: 5 }]
    )
    const { edges } = toExplorerForceGraph(state)
    expect(edges).toEqual([{ source: 'author:a', target: 'topic:t', kind: 'mentions', weight: 5 }])
  })

  it('follows edge passes through with directed: true', () => {
    const state = makeState(
      [
        { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: false },
        { id: 'author:b', kind: 'author', label: 'B', entity_id: null, is_seed: false },
      ],
      [{ source: 'author:a', target: 'author:b', kind: 'follows', directed: true }]
    )
    const { edges } = toExplorerForceGraph(state)
    expect(edges).toEqual([
      { source: 'author:a', target: 'author:b', kind: 'follows', directed: true },
    ])
  })

  it('friends edge passes through as given (undirected)', () => {
    const state = makeState(
      [
        { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: false },
        { id: 'author:b', kind: 'author', label: 'B', entity_id: null, is_seed: false },
      ],
      [{ source: 'author:a', target: 'author:b', kind: 'friends' }]
    )
    const { edges } = toExplorerForceGraph(state)
    expect(edges).toEqual([{ source: 'author:a', target: 'author:b', kind: 'friends' }])
  })

  it('no dedup — duplicate input edges both pass through', () => {
    const state = makeState(
      [
        { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: false },
        { id: 'topic:t', kind: 'topic', label: 'T', entity_id: null, is_seed: false },
      ],
      [
        { source: 'author:a', target: 'topic:t', kind: 'mentions', weight: 2 },
        { source: 'author:a', target: 'topic:t', kind: 'mentions', weight: 3 },
      ]
    )
    const { edges } = toExplorerForceGraph(state)
    expect(edges).toHaveLength(2)
  })
})

describe('toExplorerForceGraph — determinism and purity', () => {
  it('same input produces deep-equal output across calls', () => {
    const state = makeState(
      [
        { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: true },
        { id: 'topic:t', kind: 'topic', label: 'T', entity_id: null, is_seed: false },
      ],
      [{ source: 'author:a', target: 'topic:t', kind: 'mentions', weight: 4 }]
    )
    expect(toExplorerForceGraph(state)).toEqual(toExplorerForceGraph(state))
  })

  it('does not mutate the input state', () => {
    const state = makeState(
      [
        { id: 'author:a', kind: 'author', label: 'A', entity_id: null, is_seed: true },
        { id: 'topic:t', kind: 'topic', label: 'T', entity_id: null, is_seed: false },
      ],
      [{ source: 'author:a', target: 'topic:t', kind: 'mentions', weight: 4 }]
    )
    const snapshot = JSON.parse(JSON.stringify(state))
    toExplorerForceGraph(state)
    expect(state).toEqual(snapshot)
  })
})
