import { describe, it, expect } from 'vitest'
import { SOCIAL_NODE_STYLES, SOCIAL_EDGE_STYLES, toSocialForceGraph } from './socialElements'
import type { GraphState } from './graphExplorer'
import type { SocialNode, SocialEdge } from '../api/types'

function makeState(
  nodes: SocialNode[] = [],
  edges: SocialEdge[] = []
): GraphState<SocialNode, SocialEdge> {
  return { nodes, edges }
}

describe('SOCIAL_NODE_STYLES / SOCIAL_EDGE_STYLES', () => {
  it('defines exactly seed, ring1, ring2, ringN with the fixed palette', () => {
    expect(SOCIAL_NODE_STYLES).toEqual({
      seed: { color: '#fbbf24' },
      ring1: { color: '#7c3aed' },
      ring2: { color: '#64748b' },
      ringN: { color: '#b0bec5' },
    })
  })

  it('defines follows with opacity 0.7 and friends dashed', () => {
    expect(SOCIAL_EDGE_STYLES).toEqual({
      follows: { opacity: 0.7 },
      friends: { dashed: true },
    })
  })
})

describe('toSocialForceGraph — nodes', () => {
  it('returns empty nodes/edges for an empty state', () => {
    expect(toSocialForceGraph(makeState())).toEqual({ nodes: [], edges: [] })
  })

  it('is_seed=true node gets kind "seed" regardless of ring', () => {
    const state = makeState([{ id: 'author:alice', label: 'alice', ring: 0, is_seed: true }])
    const { nodes } = toSocialForceGraph(state)
    expect(nodes[0].kind).toBe('seed')
  })

  it('ring=1 node gets kind "ring1"', () => {
    const state = makeState([{ id: 'author:bob', label: 'bob', ring: 1, is_seed: false }])
    const { nodes } = toSocialForceGraph(state)
    expect(nodes[0].kind).toBe('ring1')
  })

  it('ring=2 node gets kind "ring2"', () => {
    const state = makeState([{ id: 'author:charlie', label: 'charlie', ring: 2, is_seed: false }])
    const { nodes } = toSocialForceGraph(state)
    expect(nodes[0].kind).toBe('ring2')
  })

  it('ring=3 (and beyond) node gets kind "ringN"', () => {
    const state = makeState([{ id: 'author:far', label: 'far', ring: 3, is_seed: false }])
    const { nodes } = toSocialForceGraph(state)
    expect(nodes[0].kind).toBe('ringN')
  })

  it('is_seed=true overrides the ring bucket — kind is "seed" not "ring2"', () => {
    const state = makeState([{ id: 'author:x', label: 'x', ring: 2, is_seed: true }])
    const { nodes } = toSocialForceGraph(state)
    expect(nodes[0].kind).toBe('seed')
  })

  it('node sizes follow seed 6 / ring1 3 / ring2 2 / ringN 1', () => {
    const state = makeState([
      { id: 'author:seed', label: 'seed', ring: 0, is_seed: true },
      { id: 'author:r1', label: 'r1', ring: 1, is_seed: false },
      { id: 'author:r2', label: 'r2', ring: 2, is_seed: false },
      { id: 'author:rn', label: 'rn', ring: 4, is_seed: false },
    ])
    const { nodes } = toSocialForceGraph(state)
    const byId = Object.fromEntries(nodes.map((n) => [n.id, n.size]))
    expect(byId['author:seed']).toBe(6)
    expect(byId['author:r1']).toBe(3)
    expect(byId['author:r2']).toBe(2)
    expect(byId['author:rn']).toBe(1)
  })
})

describe('toSocialForceGraph — edges', () => {
  it('follows edge is {source, target, kind: "follows", directed: true}', () => {
    const state = makeState(
      [
        { id: 'author:a', label: 'A', ring: 0, is_seed: true },
        { id: 'author:b', label: 'B', ring: 1, is_seed: false },
      ],
      [{ source: 'author:a', target: 'author:b', kind: 'follows', directed: true }]
    )
    const { edges } = toSocialForceGraph(state)
    expect(edges).toEqual([
      { source: 'author:a', target: 'author:b', kind: 'follows', directed: true },
    ])
  })

  it('friends edge has directed: false', () => {
    const state = makeState(
      [
        { id: 'author:a', label: 'A', ring: 0, is_seed: true },
        { id: 'author:b', label: 'B', ring: 1, is_seed: false },
      ],
      [{ source: 'author:a', target: 'author:b', kind: 'friends', directed: false }]
    )
    const { edges } = toSocialForceGraph(state)
    expect(edges[0].directed).toBe(false)
  })

  it('no dedup — duplicate input edges both pass through', () => {
    const state = makeState(
      [
        { id: 'author:a', label: 'A', ring: 0, is_seed: true },
        { id: 'author:b', label: 'B', ring: 1, is_seed: false },
      ],
      [
        { source: 'author:a', target: 'author:b', kind: 'follows', directed: true },
        { source: 'author:a', target: 'author:b', kind: 'follows', directed: true },
      ]
    )
    const { edges } = toSocialForceGraph(state)
    expect(edges).toHaveLength(2)
  })
})

describe('toSocialForceGraph — determinism', () => {
  it('same input produces deep-equal output across calls', () => {
    const state = makeState(
      [
        { id: 'author:a', label: 'A', ring: 0, is_seed: true },
        { id: 'author:b', label: 'B', ring: 1, is_seed: false },
      ],
      [{ source: 'author:a', target: 'author:b', kind: 'follows', directed: true }]
    )
    expect(toSocialForceGraph(state)).toEqual(toSocialForceGraph(state))
  })
})
