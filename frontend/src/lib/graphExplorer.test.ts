import { describe, expect, it } from 'vitest'
import { mergeGraph, type GraphState } from './graphExplorer'

type N = { id: string; ring?: number }
type E = { source: string; target: string; kind?: string }
const key = (e: E) => `${e.source}__${e.target}__${e.kind ?? ''}`

const base: GraphState<N, E> = {
  nodes: [{ id: 'a', ring: 0 }, { id: 'b', ring: 1 }],
  edges: [{ source: 'a', target: 'b', kind: 'follows' }]
}

describe('mergeGraph', () => {
  it('appends new nodes and edges', () => {
    const out = mergeGraph(base, { nodes: [{ id: 'c', ring: 2 }], edges: [{ source: 'b', target: 'c', kind: 'follows' }] }, key)
    expect(out.nodes.map((n) => n.id)).toEqual(['a', 'b', 'c'])
    expect(out.edges).toHaveLength(2)
  })

  it('existing node wins on id collision (keeps its ring)', () => {
    const out = mergeGraph(base, { nodes: [{ id: 'b', ring: 9 }], edges: [] }, key)
    expect(out.nodes.find((n) => n.id === 'b')?.ring).toBe(1)
  })

  it('dedupes edges by key', () => {
    const out = mergeGraph(base, { nodes: [], edges: [{ source: 'a', target: 'b', kind: 'follows' }] }, key)
    expect(out.edges).toHaveLength(1)
  })

  it('same endpoints with different kind are distinct edges', () => {
    const out = mergeGraph(base, { nodes: [], edges: [{ source: 'a', target: 'b', kind: 'friends' }] }, key)
    expect(out.edges).toHaveLength(2)
  })

  it('does not mutate inputs', () => {
    mergeGraph(base, { nodes: [{ id: 'z' }], edges: [] }, key)
    expect(base.nodes).toHaveLength(2)
  })
})
