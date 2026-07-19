import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import type { AgentTraceEntry, AppConfig } from '../api/types'

// ── mock api/client (must come before component import) ───────────────────────

vi.mock('../api/client', () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  ApiError: class ApiError extends Error {
    constructor(
      readonly status: number,
      readonly detail: unknown,
    ) {
      super(`API ${status}`)
      this.name = 'ApiError'
    }
  },
  url: (path: string) => path,
}))

vi.mock('../api/config', () => ({
  fetchConfig: vi.fn(
    (): Promise<AppConfig> =>
      Promise.resolve({ language: 'en', ingestion_enabled: false, version: '0.1.0' }),
  ),
}))

import { apiPost } from '../api/client'
import { ConfigProvider } from '../config/ConfigContext'
import { AgentGraphCard } from './AgentGraphCard'

// ── helpers ───────────────────────────────────────────────────────────────────

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <ConfigProvider>{children}</ConfigProvider>
      </QueryClientProvider>
    )
  }
}

// ── fixture data (fully synthetic) ─────────────────────────────────────────────

const NETWORK_ENTRY: AgentTraceEntry = {
  tool: 'network_around',
  arguments: { entity: 'Widgets' },
  error: null,
  result_count: 2,
  result: {
    seed: 'Widgets',
    seed_node_id: 'entity:e1',
    nodes: [
      { id: 'entity:e1', kind: 'topic', label: 'Widgets', entity_id: 'e1', is_seed: true },
      { id: 'author:u1', kind: 'author', label: 'Aria', entity_id: null, is_seed: false },
    ],
    edges: [{ source: 'author:u1', target: 'entity:e1', weight: 2 }],
    truncated: false,
  },
}

const SOCIAL_ENTRY: AgentTraceEntry = {
  tool: 'social_network_around',
  arguments: { author: 'Aria' },
  error: null,
  result_count: 2,
  result: {
    seed: 'Aria',
    seed_node_id: 'author:u1',
    nodes: [
      { id: 'author:u1', label: 'Aria', ring: 0, is_seed: true },
      { id: 'author:u2', label: 'Boo', ring: 1, is_seed: false },
    ],
    edges: [{ source: 'author:u1', target: 'author:u2', kind: 'follows', directed: true }],
    truncated: false,
  },
}

const NON_GRAPH_ENTRY: AgentTraceEntry = {
  tool: 'posts_mentioning',
  arguments: { entity: 'Widgets' },
  error: null,
  result_count: 5,
  result: null,
}

const EXPAND_NETWORK_RESULT = {
  nodes: [{ id: 'entity:e2', kind: 'topic', label: 'Gadgets', entity_id: 'e2', is_seed: false }],
  edges: [{ source: 'author:u1', target: 'entity:e2', weight: 1 }],
  truncated: false,
}

const EXPAND_NETWORK_NODE_ENTRY: AgentTraceEntry = {
  tool: 'expand_network_node',
  arguments: { node_id: 'author:auth-1', limit: 50 },
  error: null,
  result_count: 1,
  result: {
    nodes: [{ id: 'topic:ent-2', kind: 'topic', label: 'Gadgets', entity_id: 'ent-2', is_seed: false }],
    edges: [{ source: 'author:auth-1', target: 'topic:ent-2', weight: 1 }],
    truncated: false,
  },
}

const EXPAND_SOCIAL_NODE_ENTRY: AgentTraceEntry = {
  tool: 'expand_social_node',
  arguments: { author_id: 'auth-a', limit: 50 },
  error: null,
  result_count: 1,
  result: {
    nodes: [{ id: 'author:auth-b', label: 'Boo' }],
    edges: [{ source: 'author:auth-a', target: 'author:auth-b', kind: 'follows', directed: true }],
    truncated: false,
  },
}

const MALFORMED_ENTRY: AgentTraceEntry = {
  tool: 'network_around',
  arguments: {},
  error: null,
  result_count: null,
  result: {},
}

// ── tests ─────────────────────────────────────────────────────────────────────

describe('AgentGraphCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders null without a result', async () => {
    const { container } = render(<AgentGraphCard entry={NON_GRAPH_ENTRY} />, {
      wrapper: makeWrapper(),
    })
    // Wait for ConfigProvider's initial loading spinner to clear, then assert
    // the card itself rendered nothing.
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull())
    expect(container.firstChild).toBeNull()
  })

  it('renders a ForceGraph with the right node count for a network_around payload', async () => {
    const { container } = render(<AgentGraphCard entry={NETWORK_ENTRY} />, {
      wrapper: makeWrapper(),
    })
    await waitFor(() =>
      expect(container.querySelectorAll('g[role="button"]')).toHaveLength(2),
    )
  })

  it('renders for a social_network_around payload with ring styling kinds', async () => {
    render(<AgentGraphCard entry={SOCIAL_ENTRY} />, { wrapper: makeWrapper() })
    expect(await screen.findByText('Aria')).toBeTruthy()
    expect(await screen.findByText('Boo')).toBeTruthy()
  })

  it('expand action calls the right expand tool', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce(EXPAND_NETWORK_RESULT)

    render(<AgentGraphCard entry={NETWORK_ENTRY} />, { wrapper: makeWrapper() })
    await screen.findByRole('button', { name: /Aria/ })

    fireEvent.click(screen.getByRole('button', { name: /Aria/ }))
    // An author node in the unified explorer offers two expand actions
    // (topics, ties); "Expand topics" is the one that calls
    // expand_network_node.
    fireEvent.click(await screen.findByRole('button', { name: 'Expand topics' }))

    await waitFor(() => {
      const expandCall = vi
        .mocked(apiPost)
        .mock.calls.find(([path]) => path === '/tools/expand_network_node')
      expect(expandCall).toBeTruthy()
      expect(expandCall?.[1]).toMatchObject({ node_id: 'author:u1', limit: 50 })
    })
  })

  it('shows the caption with the tool name', async () => {
    render(<AgentGraphCard entry={NETWORK_ENTRY} />, { wrapper: makeWrapper() })
    expect(await screen.findByText('Graph from network_around')).toBeTruthy()
  })

  it('seeds an expand_network_node payload with the clicked anchor so edges render', async () => {
    const { container } = render(<AgentGraphCard entry={EXPAND_NETWORK_NODE_ENTRY} />, {
      wrapper: makeWrapper(),
    })
    await waitFor(() => expect(container.querySelectorAll('g[role="button"]')).toHaveLength(2))
    expect(container.querySelectorAll('line')).toHaveLength(1)
  })

  it('seeds an expand_social_node payload with the clicked anchor', async () => {
    const { container } = render(<AgentGraphCard entry={EXPAND_SOCIAL_NODE_ENTRY} />, {
      wrapper: makeWrapper(),
    })
    await waitFor(() => expect(container.querySelectorAll('g[role="button"]')).toHaveLength(2))
    expect(container.querySelectorAll('line')).toHaveLength(1)
  })

  it('renders null for a malformed result payload', async () => {
    const { container } = render(<AgentGraphCard entry={MALFORMED_ENTRY} />, {
      wrapper: makeWrapper(),
    })
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull())
    expect(container.firstChild).toBeNull()
  })
})
