import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import { useNetworkExplorer, useSocialExplorer } from './useGraphExplorer'
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
  edges: [{ source: 'topic:ent-1', target: 'author:auth-1', weight: 1 }],
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

describe('useNetworkExplorer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('seedFrom replaces the graph and clears selection', () => {
    const { result } = renderHook(() => useNetworkExplorer(), { wrapper: makeWrapper() })

    act(() => result.current.seedFrom(networkSeed))
    expect(result.current.graph?.nodes).toHaveLength(2)

    act(() => result.current.select('author:auth-1'))
    expect(result.current.selectedId).toBe('author:auth-1')

    act(() => result.current.seedFrom(networkSeed))
    expect(result.current.selectedId).toBeNull()
  })

  it('expand merges neighbour payload and tracks expandingId', async () => {
    const expandOut: ExpandNetworkNodeOut = {
      nodes: [{ id: 'topic:ent-2', kind: 'topic', label: 'Entity Two', entity_id: 'ent-2', is_seed: false }],
      edges: [{ source: 'author:auth-1', target: 'topic:ent-2', weight: 1 }],
      truncated: false
    }
    let resolveCall: (out: ExpandNetworkNodeOut) => void = () => {}
    vi.mocked(callTool).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveCall = resolve
      })
    )

    const { result } = renderHook(() => useNetworkExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFrom(networkSeed))

    act(() => result.current.expand('author:auth-1'))
    expect(result.current.expandingId).toBe('author:auth-1')

    act(() => resolveCall(expandOut))
    await waitFor(() => expect(result.current.expandingId).toBeNull())

    expect(result.current.graph?.nodes).toHaveLength(3)
    expect(callTool).toHaveBeenCalledWith('expand_network_node', { node_id: 'author:auth-1', limit: 50 })
  })

  it('expansionTruncated reflects the last expansion', async () => {
    const { result } = renderHook(() => useNetworkExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFrom(networkSeed))

    vi.mocked(callTool).mockResolvedValueOnce({ nodes: [], edges: [], truncated: true } as ExpandNetworkNodeOut)
    act(() => result.current.expand('author:auth-1'))
    await waitFor(() => expect(result.current.expansionTruncated).toBe(true))

    vi.mocked(callTool).mockResolvedValueOnce({ nodes: [], edges: [], truncated: false } as ExpandNetworkNodeOut)
    act(() => result.current.expand('author:auth-1'))
    await waitFor(() => expect(result.current.expansionTruncated).toBe(false))
  })

  it('expand is a no-op while another expansion is in flight', async () => {
    vi.mocked(callTool).mockReturnValueOnce(new Promise(() => {}))

    const { result } = renderHook(() => useNetworkExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFrom(networkSeed))

    act(() => result.current.expand('author:auth-1'))
    await waitFor(() => expect(callTool).toHaveBeenCalledTimes(1))
    act(() => result.current.expand('topic:ent-1'))

    expect(callTool).toHaveBeenCalledTimes(1)
  })

  it('removeNode drops the node and its incident edges', () => {
    const { result } = renderHook(() => useNetworkExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFrom(networkSeed))

    act(() => result.current.removeNode('author:auth-1'))

    expect(result.current.graph?.nodes.map((n) => n.id)).toEqual(['topic:ent-1'])
    expect(result.current.graph?.edges).toHaveLength(0)
  })

  it('removeNode clears selection when the removed node was selected', () => {
    const { result } = renderHook(() => useNetworkExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFrom(networkSeed))
    act(() => result.current.select('author:auth-1'))

    act(() => result.current.removeNode('author:auth-1'))

    expect(result.current.selectedId).toBeNull()
  })

  it('removeNode keeps selection when a different node was selected', () => {
    const { result } = renderHook(() => useNetworkExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFrom(networkSeed))
    act(() => result.current.select('topic:ent-1'))

    act(() => result.current.removeNode('author:auth-1'))

    expect(result.current.selectedId).toBe('topic:ent-1')
  })

  it('a removed node can be re-added by expanding a neighbour', async () => {
    const expandOut: ExpandNetworkNodeOut = {
      nodes: [{ id: 'author:auth-1', kind: 'author', label: 'Author One', entity_id: null, is_seed: false }],
      edges: [{ source: 'topic:ent-1', target: 'author:auth-1', weight: 1 }],
      truncated: false
    }
    vi.mocked(callTool).mockResolvedValueOnce(expandOut)

    const { result } = renderHook(() => useNetworkExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFrom(networkSeed))
    act(() => result.current.removeNode('author:auth-1'))
    expect(result.current.graph?.nodes.map((n) => n.id)).toEqual(['topic:ent-1'])

    act(() => result.current.expand('topic:ent-1'))
    await waitFor(() => expect(result.current.expandingId).toBeNull())

    expect(result.current.graph?.nodes.map((n) => n.id)).toContain('author:auth-1')
  })
})

describe('useSocialExplorer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('expand strips the author: prefix and assigns ring+1', async () => {
    const expandOut: ExpandSocialNodeOut = {
      nodes: [{ id: 'author:auth-c', label: 'Author C' }],
      edges: [{ source: 'author:auth-b', target: 'author:auth-c', kind: 'follows', directed: true }],
      truncated: false
    }
    vi.mocked(callTool).mockResolvedValueOnce(expandOut)

    const { result } = renderHook(() => useSocialExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFrom(socialSeed))

    act(() => result.current.expand('author:auth-b'))
    await waitFor(() => expect(result.current.expandingId).toBeNull())

    expect(callTool).toHaveBeenCalledWith('expand_social_node', { author_id: 'auth-b', limit: 50 })
    const added = result.current.graph?.nodes.find((n) => n.id === 'author:auth-c')
    expect(added?.ring).toBe(2)
    expect(added?.is_seed).toBe(false)
  })

  it('expand surfaces an error message on failure', async () => {
    vi.mocked(callTool).mockRejectedValueOnce(new Error('boom'))

    const { result } = renderHook(() => useSocialExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFrom(socialSeed))

    act(() => result.current.expand('author:auth-b'))
    await waitFor(() => expect(result.current.expandingId).toBeNull())

    expect(result.current.expandError).toBe('boom')
  })

  it('ring bookkeeping survives back-edges to already-known nodes', async () => {
    // Seed: auth-a ring 0, auth-b ring 1
    const { result } = renderHook(() => useSocialExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFrom(socialSeed))

    // Expand auth-b: returns auth-a (back-edge, already in graph at ring 0) and auth-c (new)
    const expandBOut: ExpandSocialNodeOut = {
      nodes: [
        { id: 'author:auth-a', label: 'Author A' },
        { id: 'author:auth-c', label: 'Author C' }
      ],
      edges: [
        { source: 'author:auth-b', target: 'author:auth-a', kind: 'follows', directed: true },
        { source: 'author:auth-b', target: 'author:auth-c', kind: 'follows', directed: true }
      ],
      truncated: false
    }
    vi.mocked(callTool).mockResolvedValueOnce(expandBOut)

    act(() => result.current.expand('author:auth-b'))
    await waitFor(() => expect(result.current.expandingId).toBeNull())

    // After merge: auth-a still ring 0, auth-c newly added as ring 2
    const authA = result.current.graph?.nodes.find((n) => n.id === 'author:auth-a')
    const authC = result.current.graph?.nodes.find((n) => n.id === 'author:auth-c')
    expect(authA?.ring).toBe(0)
    expect(authC?.ring).toBe(2)

    // Expand auth-a again: should assign its new neighbours ring 1 (not 3)
    const expandAOut: ExpandSocialNodeOut = {
      nodes: [{ id: 'author:auth-d', label: 'Author D' }],
      edges: [{ source: 'author:auth-a', target: 'author:auth-d', kind: 'follows', directed: true }],
      truncated: false
    }
    vi.mocked(callTool).mockResolvedValueOnce(expandAOut)

    act(() => result.current.expand('author:auth-a'))
    await waitFor(() => expect(result.current.expandingId).toBeNull())

    // auth-d should be ring 1 (auth-a's ring 0 + 1), not 3
    const authD = result.current.graph?.nodes.find((n) => n.id === 'author:auth-d')
    expect(authD?.ring).toBe(1)
  })

  it('expand is a no-op while another expansion is in flight', async () => {
    vi.mocked(callTool).mockReturnValueOnce(new Promise(() => {}))

    const { result } = renderHook(() => useSocialExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFrom(socialSeed))

    act(() => result.current.expand('author:auth-a'))
    await waitFor(() => expect(callTool).toHaveBeenCalledTimes(1))
    act(() => result.current.expand('author:auth-b'))

    expect(callTool).toHaveBeenCalledTimes(1)
  })

  it('removeNode drops the node and its incident edges', () => {
    const { result } = renderHook(() => useSocialExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFrom(socialSeed))

    act(() => result.current.removeNode('author:auth-b'))

    expect(result.current.graph?.nodes.map((n) => n.id)).toEqual(['author:auth-a'])
    expect(result.current.graph?.edges).toHaveLength(0)
  })

  it('removeNode clears selection when the removed node was selected', () => {
    const { result } = renderHook(() => useSocialExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFrom(socialSeed))
    act(() => result.current.select('author:auth-b'))

    act(() => result.current.removeNode('author:auth-b'))

    expect(result.current.selectedId).toBeNull()
  })

  it('removeNode keeps selection when a different node was selected', () => {
    const { result } = renderHook(() => useSocialExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFrom(socialSeed))
    act(() => result.current.select('author:auth-a'))

    act(() => result.current.removeNode('author:auth-b'))

    expect(result.current.selectedId).toBe('author:auth-a')
  })

  it('a removed node can be re-added by expanding a neighbour', async () => {
    const expandOut: ExpandSocialNodeOut = {
      nodes: [{ id: 'author:auth-b', label: 'Author B' }],
      edges: [{ source: 'author:auth-a', target: 'author:auth-b', kind: 'follows', directed: true }],
      truncated: false
    }
    vi.mocked(callTool).mockResolvedValueOnce(expandOut)

    const { result } = renderHook(() => useSocialExplorer(), { wrapper: makeWrapper() })
    act(() => result.current.seedFrom(socialSeed))
    act(() => result.current.removeNode('author:auth-b'))
    expect(result.current.graph?.nodes.map((n) => n.id)).toEqual(['author:auth-a'])

    act(() => result.current.expand('author:auth-a'))
    await waitFor(() => expect(result.current.expandingId).toBeNull())

    expect(result.current.graph?.nodes.map((n) => n.id)).toContain('author:auth-b')
  })
})
