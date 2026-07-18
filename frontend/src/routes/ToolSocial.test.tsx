import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import type { AppConfig } from '../api/types'

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
import { ToolSocial } from './ToolSocial'

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

async function submit(author = 'alice') {
  await screen.findByText('social network around an author')
  const input = screen.getAllByRole('textbox')[0]
  fireEvent.change(input, { target: { value: author } })
  fireEvent.click(screen.getByRole('button', { name: /build network/i }))
}

// ── fixture data ──────────────────────────────────────────────────────────────

const SEED_RESULT = {
  seed: 'alice',
  seed_node_id: 'author:u1',
  nodes: [
    { id: 'author:u1', label: 'Alice', ring: 0, is_seed: true },
    { id: 'author:u2', label: 'Bob', ring: 1, is_seed: false },
  ],
  edges: [{ source: 'author:u1', target: 'author:u2', kind: 'follows', directed: true }],
  truncated: false,
}

const EXPAND_RESULT = {
  nodes: [{ id: 'author:u3', label: 'Carol' }],
  edges: [{ source: 'author:u2', target: 'author:u3', kind: 'friends', directed: false }],
  truncated: false,
}

describe('ToolSocial', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('submit form renders ForceGraph with one node per graph node', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce(SEED_RESULT)

    const { container } = render(<ToolSocial />, { wrapper: makeWrapper() })
    await submit()

    await waitFor(() =>
      expect(container.querySelectorAll('g[role="button"]')).toHaveLength(2),
    )
  })

  it('shows an info banner when the seed result is truncated', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({ ...SEED_RESULT, truncated: true })

    render(<ToolSocial />, { wrapper: makeWrapper() })
    await submit()

    expect(await screen.findByText(/capped view/i)).toBeTruthy()
  })

  it('selecting a node then clicking Expand node calls the expand tool and merges the neighbour', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce(SEED_RESULT).mockResolvedValueOnce(EXPAND_RESULT)

    const { container } = render(<ToolSocial />, { wrapper: makeWrapper() })
    await submit()
    await waitFor(() => expect(container.querySelectorAll('g[role="button"]')).toHaveLength(2))

    fireEvent.click(screen.getByRole('button', { name: /Bob/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Expand node' }))

    await waitFor(() => expect(container.querySelectorAll('g[role="button"]')).toHaveLength(3))

    const expandCall = vi
      .mocked(apiPost)
      .mock.calls.find(([path]) => path === '/tools/expand_social_node')
    expect(expandCall).toBeTruthy()
    expect(expandCall?.[1]).toMatchObject({ author_id: 'u2', limit: 50 })
  })

  it('shows the expansion-capped banner when the expand result is truncated', async () => {
    vi.mocked(apiPost)
      .mockResolvedValueOnce(SEED_RESULT)
      .mockResolvedValueOnce({ ...EXPAND_RESULT, truncated: true })

    render(<ToolSocial />, { wrapper: makeWrapper() })
    await submit()
    await screen.findByRole('button', { name: /Bob/ })

    fireEvent.click(screen.getByRole('button', { name: /Bob/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Expand node' }))

    expect(await screen.findByText(/expansion capped.*50 neighbors/i)).toBeTruthy()
  })

  it('shows an expand-failed banner when the expand call rejects', async () => {
    vi.mocked(apiPost)
      .mockResolvedValueOnce(SEED_RESULT)
      .mockRejectedValueOnce(new Error('boom'))

    render(<ToolSocial />, { wrapper: makeWrapper() })
    await submit()
    await screen.findByRole('button', { name: /Bob/ })

    fireEvent.click(screen.getByRole('button', { name: /Bob/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Expand node' }))

    expect(await screen.findByText(/Expansion failed: boom/i)).toBeTruthy()
  })

  it('shows empty-state text and no svg for an empty seed result', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({
      seed: 'ghost',
      seed_node_id: null,
      nodes: [],
      edges: [],
      truncated: false,
    })

    const { container } = render(<ToolSocial />, { wrapper: makeWrapper() })
    await submit('ghost')

    expect(await screen.findByText('no network — the author matched nothing')).toBeTruthy()
    expect(container.querySelector('svg')).toBeNull()
  })
})
