import { describe, it, expect } from 'vitest'
import { toSocialElements } from './socialElements'
import type { SocialNetworkAroundOut } from '../api/types'

// Helpers
function makeOut(overrides: Partial<SocialNetworkAroundOut> = {}): SocialNetworkAroundOut {
  return {
    seed: 'Alice',
    seed_node_id: 'author:alice',
    nodes: [],
    edges: [],
    truncated: false,
    ...overrides,
  }
}

describe('toSocialElements — nodes', () => {
  it('returns an empty array for an empty network', () => {
    expect(toSocialElements(makeOut())).toEqual([])
  })

  it('is_seed=true node always gets class "seed", regardless of ring', () => {
    const out = makeOut({
      nodes: [{ id: 'author:alice', label: 'alice', ring: 0, is_seed: true }],
    })
    const elements = toSocialElements(out)
    const node = elements[0]
    expect((node.classes as string).split(' ')).toContain('seed')
  })

  it('ring=0 node also gets class "seed" (even if is_seed=false)', () => {
    const out = makeOut({
      nodes: [{ id: 'author:alice', label: 'alice', ring: 0, is_seed: false }],
    })
    const elements = toSocialElements(out)
    expect((elements[0].classes as string).split(' ')).toContain('seed')
  })

  it('ring=1 node gets class "ring1"', () => {
    const out = makeOut({
      nodes: [{ id: 'author:bob', label: 'bob', ring: 1, is_seed: false }],
    })
    const elements = toSocialElements(out)
    const classes = (elements[0].classes as string).split(' ')
    expect(classes).toContain('ring1')
    expect(classes).not.toContain('seed')
  })

  it('ring=2 node gets class "ring2"', () => {
    const out = makeOut({
      nodes: [{ id: 'author:charlie', label: 'charlie', ring: 2, is_seed: false }],
    })
    const elements = toSocialElements(out)
    const classes = (elements[0].classes as string).split(' ')
    expect(classes).toContain('ring2')
    expect(classes).not.toContain('seed')
    expect(classes).not.toContain('ring1')
  })

  it('ring=3 node gets class "ringN"', () => {
    const out = makeOut({
      nodes: [{ id: 'author:far', label: 'far', ring: 3, is_seed: false }],
    })
    const elements = toSocialElements(out)
    const classes = (elements[0].classes as string).split(' ')
    expect(classes).toContain('ringN')
  })

  it('ring=5 node also gets class "ringN"', () => {
    const out = makeOut({
      nodes: [{ id: 'author:deep', label: 'deep', ring: 5, is_seed: false }],
    })
    const elements = toSocialElements(out)
    expect((elements[0].classes as string).split(' ')).toContain('ringN')
  })

  it('is_seed=true overrides the ring bucket — gets "seed" not "ring2"', () => {
    // If backend ever sends ring=2 with is_seed=true, seed wins
    const out = makeOut({
      nodes: [{ id: 'author:x', label: 'x', ring: 2, is_seed: true }],
    })
    const elements = toSocialElements(out)
    const classes = (elements[0].classes as string).split(' ')
    expect(classes).toContain('seed')
    expect(classes).not.toContain('ring2')
  })

  it('node data carries id, label, ring, isSeed', () => {
    const out = makeOut({
      nodes: [{ id: 'author:alice', label: 'Alice', ring: 0, is_seed: true }],
    })
    const node = toSocialElements(out)[0]
    expect(node.data.id).toBe('author:alice')
    expect(node.data.label).toBe('Alice')
    expect(node.data.ring).toBe(0)
    expect(node.data.isSeed).toBe(true)
  })
})

describe('toSocialElements — edges', () => {
  it('edge id is "${source}__${target}"', () => {
    const out = makeOut({
      nodes: [
        { id: 'author:a', label: 'A', ring: 0, is_seed: true },
        { id: 'author:b', label: 'B', ring: 1, is_seed: false },
      ],
      edges: [{ source: 'author:a', target: 'author:b', kind: 'follows', directed: true }],
    })
    const elements = toSocialElements(out)
    const edge = elements.find((e) => e.data.source)!
    expect(edge.data.id).toBe('author:a__author:b')
  })

  it('duplicate edges (same source+target) are deduped to one', () => {
    const out = makeOut({
      nodes: [
        { id: 'author:a', label: 'A', ring: 0, is_seed: true },
        { id: 'author:b', label: 'B', ring: 1, is_seed: false },
      ],
      edges: [
        { source: 'author:a', target: 'author:b', kind: 'follows', directed: true },
        { source: 'author:a', target: 'author:b', kind: 'follows', directed: true },
      ],
    })
    const elements = toSocialElements(out)
    const edgeElements = elements.filter((e) => e.data.source)
    expect(edgeElements).toHaveLength(1)
  })

  it('follows edge gets class "follows"', () => {
    const out = makeOut({
      nodes: [
        { id: 'author:a', label: 'A', ring: 0, is_seed: true },
        { id: 'author:b', label: 'B', ring: 1, is_seed: false },
      ],
      edges: [{ source: 'author:a', target: 'author:b', kind: 'follows', directed: true }],
    })
    const elements = toSocialElements(out)
    const edge = elements.find((e) => e.data.source)!
    expect(edge.classes).toBe('follows')
  })

  it('friends edge gets class "friends"', () => {
    const out = makeOut({
      nodes: [
        { id: 'author:a', label: 'A', ring: 0, is_seed: true },
        { id: 'author:b', label: 'B', ring: 1, is_seed: false },
      ],
      edges: [{ source: 'author:a', target: 'author:b', kind: 'friends', directed: false }],
    })
    const elements = toSocialElements(out)
    const edge = elements.find((e) => e.data.source)!
    expect(edge.classes).toBe('friends')
  })

  it('directed flag is preserved on edge data', () => {
    const out = makeOut({
      nodes: [
        { id: 'author:a', label: 'A', ring: 0, is_seed: true },
        { id: 'author:b', label: 'B', ring: 1, is_seed: false },
      ],
      edges: [{ source: 'author:a', target: 'author:b', kind: 'follows', directed: true }],
    })
    const elements = toSocialElements(out)
    const edge = elements.find((e) => e.data.source)!
    expect(edge.data.directed).toBe(true)
  })

  it('friends edge has directed=false', () => {
    const out = makeOut({
      nodes: [
        { id: 'author:a', label: 'A', ring: 0, is_seed: true },
        { id: 'author:b', label: 'B', ring: 1, is_seed: false },
      ],
      edges: [{ source: 'author:a', target: 'author:b', kind: 'friends', directed: false }],
    })
    const elements = toSocialElements(out)
    const edge = elements.find((e) => e.data.source)!
    expect(edge.data.directed).toBe(false)
  })

  it('edge data carries source, target, kind', () => {
    const out = makeOut({
      nodes: [
        { id: 'author:x', label: 'X', ring: 0, is_seed: true },
        { id: 'author:y', label: 'Y', ring: 1, is_seed: false },
      ],
      edges: [{ source: 'author:x', target: 'author:y', kind: 'friends', directed: false }],
    })
    const elements = toSocialElements(out)
    const edge = elements.find((e) => e.data.source)!
    expect(edge.data.source).toBe('author:x')
    expect(edge.data.target).toBe('author:y')
    expect(edge.data.kind).toBe('friends')
  })
})
