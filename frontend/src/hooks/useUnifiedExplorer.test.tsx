import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import { useUnifiedExplorer } from './useUnifiedExplorer'
import type {
  ExpandNetworkNodeOut,
  ExpandSocialNodeOut,
  NetworkAroundOut,
  SocialNetworkAroundOut
} from '../api/types'

vi.mock('../api/tools', () => ({
  callTool: vi.fn()
}))

import { callTool } from '../api/tools'

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false }
    }
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

const networkSeed: NetworkAroundOut = {
  seed: 'ent-1',
  seed_node_id: 'topic:ent-1',
  nodes: [
    { id: 'topic:ent-1', kind: 'topic', label: 'Entity One', entity_id: 'ent-1', is_seed: true },
    { id: 'author:auth-1', kind: 'author', label: 'Author One', entity_id: null, is_seed: false }
  ],
  edges: [{ source: 'topic:ent-1', target: 'author:auth-1', weight: 3 }],
  truncated: false
}

const socialSeed: SocialNetworkAroundOut = {
  seed: 'auth-a',
  seed_node_id: 'author:auth-a',
  nodes: [
    { id: 'author:auth-a', label: 'Author A', ring: 0, is_seed: true },
    { id: 'author:auth-b', label: 'Author B', ring: 1, is_seed: false }
  ],
  edges: [{ source: 'author:auth-a', target: 'author:auth-b', kind: 'follows', directed: true }],
  truncated: false
}

describe('useUnifiedExplorer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('seedFromNetwork maps nodes 1:1 and edges to kind mentions', () => {
    const { result } = renderHook(() => useUnifiedExplorer(), { wrapper: makeWrapper() })

    act(() => result.current.seedFromNetwork(networkSeed))

    expect(result.current.graph?.nodes).toEqual(networkSeed.nodes)
    expect(result.current.graph?.edges).toEqual([
      { source: 'topic:ent-1', target: 'author:auth-1', kind: 'mentions', weight: 3 }
    ])
  })

  it('seedFromNetwork clears selection and truncation', () => {
    const { result } = renderHook(() => useUnifiedExplorer(), { wrapper: makeWrapper() })

    act(() => result.current.seedFromNetwork(networkSeed))
    act(() => result.current.select(['author:auth-1']))
    expect(result.current.selectedIds).toEqual(['author:auth-1'])

    act(() => result.current.seedFromNetwork(networkSeed))
    expect(result.current.selectedIds).toEqual([])
  })

  it('seedFromSocial maps nodes to author kind and discards ring', () => {
    const { result } = renderHook(() => useUnifiedExplorer(), { wrapper: makeWrapper() })

    act(() => result.current.seedFromSocial(socialSeed))

    expect(result.current.graph?.nodes).toEqual([
      { id: 'author:auth-a', kind: 'author', label: 'Author A', entity_id: null, is_seed: true },
      { id: 'author:auth-b', kind: 'author', label: 'Author B', entity_id: null, is_seed: false }
    ])
    expect(result.current.graph?.edges).toEqual([
      { source: 'author:auth-a', target: 'author:auth-b', kind: 'follows', directed: true }
    ])
  })

  it('seedFromSocial clears selection and truncation', () => {
    const { result } = renderHook(() => useUnifiedExplorer(), { wrapper: makeWrapper() })

    act(() => result.current.seedFromSocial(socialSeed))
    act(() => result.current.select(['author:auth-a']))
    expect(result.current.selectedIds).toEqual(['author:auth-a'])

    act(() => result.current.seedFromSocial(socialSeed))
    expect(result.current.selectedIds).toEqual([])
  })

  it('payoff: expandTies on a network-seeded author unifies mentions + follows on one node', async () => {
    const tiesOut: ExpandSocialNodeOut = {
      nodes: [{ id: 'author:auth-2', label: 'Author Two' }],
      edges: [{ source: 'author:auth-1', target: 'author:auth-2', kind: 'follows', directed: true }],
      truncated: false
    }
    vi.mocked(callTool).mockResolvedValueOnce(tiesOut)

    const { result } = renderHook(() => useUnifiedExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFromNetwork(networkSeed))

    act(() => result.current.expandTies('author:auth-1'))
    await waitFor(() => expect(result.current.expandingId).toBeNull())

    expect(callTool).toHaveBeenCalledWith('expand_social_node', { author_id: 'auth-1', limit: 50 })

    const nodeIds = result.current.graph?.nodes.map((n) => n.id) ?? []
    // No duplicate auth-1 node — appeared once from the seed, expansion only adds auth-2.
    expect(nodeIds.filter((id) => id === 'author:auth-1')).toHaveLength(1)
    expect(nodeIds).toEqual(['topic:ent-1', 'author:auth-1', 'author:auth-2'])

    const edges = result.current.graph?.edges ?? []
    const mentionsEdge = edges.find((e) => e.kind === 'mentions')
    const followsEdge = edges.find((e) => e.kind === 'follows')
    expect(mentionsEdge).toEqual({ source: 'topic:ent-1', target: 'author:auth-1', kind: 'mentions', weight: 3 })
    expect(followsEdge).toEqual({ source: 'author:auth-1', target: 'author:auth-2', kind: 'follows', directed: true })
    expect(edges).toHaveLength(2)
  })

  it('expandTopics calls expand_network_node with node_id and maps result', async () => {
    const out: ExpandNetworkNodeOut = {
      nodes: [{ id: 'topic:ent-2', kind: 'topic', label: 'Entity Two', entity_id: 'ent-2', is_seed: false }],
      edges: [{ source: 'author:auth-1', target: 'topic:ent-2', weight: 5 }],
      truncated: false
    }
    vi.mocked(callTool).mockResolvedValueOnce(out)

    const { result } = renderHook(() => useUnifiedExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFromNetwork(networkSeed))

    act(() => result.current.expandTopics('author:auth-1'))
    await waitFor(() => expect(result.current.expandingId).toBeNull())

    expect(callTool).toHaveBeenCalledWith('expand_network_node', { node_id: 'author:auth-1', limit: 50 })
    expect(result.current.graph?.nodes.map((n) => n.id)).toContain('topic:ent-2')
    expect(result.current.graph?.edges).toContainEqual({
      source: 'author:auth-1',
      target: 'topic:ent-2',
      kind: 'mentions',
      weight: 5
    })
  })

  it('expandTopic calls expand_network_node with the topic node_id', async () => {
    const out: ExpandNetworkNodeOut = {
      nodes: [{ id: 'author:auth-3', kind: 'author', label: 'Author Three', entity_id: null, is_seed: false }],
      edges: [{ source: 'topic:ent-1', target: 'author:auth-3', weight: 1 }],
      truncated: false
    }
    vi.mocked(callTool).mockResolvedValueOnce(out)

    const { result } = renderHook(() => useUnifiedExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFromNetwork(networkSeed))

    act(() => result.current.expandTopic('topic:ent-1'))
    await waitFor(() => expect(result.current.expandingId).toBeNull())

    expect(callTool).toHaveBeenCalledWith('expand_network_node', { node_id: 'topic:ent-1', limit: 50 })
    expect(result.current.graph?.nodes.map((n) => n.id)).toContain('author:auth-3')
  })

  it('cross-family edge dedup by key: expanding twice does not duplicate the follows edge', async () => {
    const tiesOut: ExpandSocialNodeOut = {
      nodes: [{ id: 'author:auth-2', label: 'Author Two' }],
      edges: [{ source: 'author:auth-1', target: 'author:auth-2', kind: 'follows', directed: true }],
      truncated: false
    }
    vi.mocked(callTool).mockResolvedValueOnce(tiesOut)
    vi.mocked(callTool).mockResolvedValueOnce(tiesOut)

    const { result } = renderHook(() => useUnifiedExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFromNetwork(networkSeed))

    act(() => result.current.expandTies('author:auth-1'))
    await waitFor(() => expect(result.current.expandingId).toBeNull())
    act(() => result.current.expandTies('author:auth-1'))
    await waitFor(() => expect(result.current.expandingId).toBeNull())

    const followsEdges = result.current.graph?.edges.filter((e) => e.kind === 'follows') ?? []
    expect(followsEdges).toHaveLength(1)
    expect(result.current.graph?.nodes.filter((n) => n.id === 'author:auth-2')).toHaveLength(1)
  })

  it('is a no-op while another expansion is in flight, across different expand fns', async () => {
    vi.mocked(callTool).mockReturnValueOnce(new Promise(() => {}))

    const { result } = renderHook(() => useUnifiedExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFromNetwork(networkSeed))

    act(() => result.current.expandTopics('author:auth-1'))
    await waitFor(() => expect(callTool).toHaveBeenCalledTimes(1))
    act(() => result.current.expandTies('author:auth-1'))

    expect(callTool).toHaveBeenCalledTimes(1)
  })

  it('discards an in-flight expansion of a node removed before it resolves', async () => {
    const out: ExpandSocialNodeOut = {
      nodes: [{ id: 'author:auth-2', label: 'Author Two' }],
      edges: [{ source: 'author:auth-1', target: 'author:auth-2', kind: 'follows', directed: true }],
      truncated: true
    }
    let resolveCall: (out: ExpandSocialNodeOut) => void = () => {}
    vi.mocked(callTool).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveCall = resolve
      })
    )

    const { result } = renderHook(() => useUnifiedExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFromNetwork(networkSeed))

    act(() => result.current.expandTies('author:auth-1'))
    expect(result.current.expandingId).toBe('author:auth-1')

    act(() => result.current.removeNodes(['author:auth-1']))
    expect(result.current.graph?.nodes.map((n) => n.id)).toEqual(['topic:ent-1'])

    act(() => resolveCall(out))
    await waitFor(() => expect(result.current.expandingId).toBeNull())

    expect(result.current.graph?.nodes.map((n) => n.id)).toEqual(['topic:ent-1'])
    expect(result.current.graph?.edges).toHaveLength(0)
    expect(result.current.expansionTruncated).toBe(false)
  })

  it('removeNodes drops multiple nodes and every incident edge, adjusting selection', () => {
    const { result } = renderHook(() => useUnifiedExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFromNetwork(networkSeed))
    act(() => result.current.select(['author:auth-1', 'topic:ent-1']))

    act(() => result.current.removeNodes(['author:auth-1']))

    expect(result.current.graph?.nodes.map((n) => n.id)).toEqual(['topic:ent-1'])
    expect(result.current.graph?.edges).toHaveLength(0)
    expect(result.current.selectedIds).toEqual(['topic:ent-1'])
  })

  it('expansionTruncated reflects the last expansion', async () => {
    const { result } = renderHook(() => useUnifiedExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFromNetwork(networkSeed))

    vi.mocked(callTool).mockResolvedValueOnce({ nodes: [], edges: [], truncated: true } as ExpandNetworkNodeOut)
    act(() => result.current.expandTopics('author:auth-1'))
    await waitFor(() => expect(result.current.expansionTruncated).toBe(true))

    vi.mocked(callTool).mockResolvedValueOnce({ nodes: [], edges: [], truncated: false } as ExpandNetworkNodeOut)
    act(() => result.current.expandTopics('author:auth-1'))
    await waitFor(() => expect(result.current.expansionTruncated).toBe(false))
  })

  it('surfaces an error message on expansion failure', async () => {
    vi.mocked(callTool).mockRejectedValueOnce(new Error('boom'))

    const { result } = renderHook(() => useUnifiedExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFromNetwork(networkSeed))

    act(() => result.current.expandTies('author:auth-1'))
    await waitFor(() => expect(result.current.expandingId).toBeNull())

    expect(result.current.expandError).toBe('boom')
  })

  it('clears stale expand error on reseed from network', async () => {
    vi.mocked(callTool).mockRejectedValueOnce(new Error('expansion failed'))

    const { result } = renderHook(() => useUnifiedExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFromNetwork(networkSeed))

    // Trigger an expansion that fails
    act(() => result.current.expandTies('author:auth-1'))
    await waitFor(() => expect(result.current.expandingId).toBeNull())
    expect(result.current.expandError).toBe('expansion failed')

    // Reseed with new payload — error should clear
    act(() => result.current.seedFromNetwork(networkSeed))
    expect(result.current.expandError).toBeNull()
  })

  it('clears stale expand error on reseed from social', async () => {
    vi.mocked(callTool).mockRejectedValueOnce(new Error('expansion failed'))

    const { result } = renderHook(() => useUnifiedExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFromSocial(socialSeed))

    // Trigger an expansion that fails
    act(() => result.current.expandTopics('topic:ent-1'))
    await waitFor(() => expect(result.current.expandingId).toBeNull())
    expect(result.current.expandError).toBe('expansion failed')

    // Reseed with new payload — error should clear
    act(() => result.current.seedFromSocial(socialSeed))
    expect(result.current.expandError).toBeNull()
  })
})
