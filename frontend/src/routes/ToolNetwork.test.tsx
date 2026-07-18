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
import { ToolNetwork } from './ToolNetwork'

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

async function submit(entity = 'Climate') {
  await screen.findByText('network around an entity')
  const input = screen.getAllByRole('textbox')[0]
  fireEvent.change(input, { target: { value: entity } })
  fireEvent.click(screen.getByRole('button', { name: /build network/i }))
}

// ── fixture data ──────────────────────────────────────────────────────────────

const SEED_RESULT = {
  seed: 'Climate',
  seed_node_id: 'entity:e1',
  nodes: [
    { id: 'entity:e1', kind: 'topic', label: 'Climate', entity_id: 'e1', is_seed: true },
    { id: 'author:u1', kind: 'author', label: 'Alice', entity_id: null, is_seed: false },
  ],
  edges: [{ source: 'author:u1', target: 'entity:e1', weight: 3 }],
  truncated: false,
}

const EXPAND_RESULT = {
  nodes: [
    { id: 'entity:e2', kind: 'topic', label: 'Policy', entity_id: 'e2', is_seed: false },
  ],
  edges: [{ source: 'author:u1', target: 'entity:e2', weight: 1 }],
  truncated: false,
}

describe('ToolNetwork', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('submit form renders ForceGraph with one node per graph node', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce(SEED_RESULT)

    const { container } = render(<ToolNetwork />, { wrapper: makeWrapper() })
    await submit()

    await waitFor(() =>
      expect(container.querySelectorAll('g[role="button"]')).toHaveLength(2),
    )
  })

  it('shows an info banner when the seed result is truncated', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({ ...SEED_RESULT, truncated: true })

    render(<ToolNetwork />, { wrapper: makeWrapper() })
    await submit()

    expect(await screen.findByText(/capped view/i)).toBeTruthy()
  })

  it('selecting a node then clicking Expand node calls the expand tool and merges the neighbour', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce(SEED_RESULT).mockResolvedValueOnce(EXPAND_RESULT)

    const { container } = render(<ToolNetwork />, { wrapper: makeWrapper() })
    await submit()
    await waitFor(() => expect(container.querySelectorAll('g[role="button"]')).toHaveLength(2))

    fireEvent.click(screen.getByRole('button', { name: /Alice/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Expand node' }))

    await waitFor(() => expect(container.querySelectorAll('g[role="button"]')).toHaveLength(3))

    const expandCall = vi
      .mocked(apiPost)
      .mock.calls.find(([path]) => path === '/tools/expand_network_node')
    expect(expandCall).toBeTruthy()
    expect(expandCall?.[1]).toMatchObject({ node_id: 'author:u1', limit: 50 })
  })

  it('shows the expansion-capped banner when the expand result is truncated', async () => {
    vi.mocked(apiPost)
      .mockResolvedValueOnce(SEED_RESULT)
      .mockResolvedValueOnce({ ...EXPAND_RESULT, truncated: true })

    render(<ToolNetwork />, { wrapper: makeWrapper() })
    await submit()
    await screen.findByRole('button', { name: /Alice/ })

    fireEvent.click(screen.getByRole('button', { name: /Alice/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Expand node' }))

    expect(await screen.findByText(/expansion capped.*50 neighbors/i)).toBeTruthy()
  })

  it('shows an expand-failed banner when the expand call rejects', async () => {
    vi.mocked(apiPost)
      .mockResolvedValueOnce(SEED_RESULT)
      .mockRejectedValueOnce(new Error('boom'))

    render(<ToolNetwork />, { wrapper: makeWrapper() })
    await submit()
    await screen.findByRole('button', { name: /Alice/ })

    fireEvent.click(screen.getByRole('button', { name: /Alice/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Expand node' }))

    expect(await screen.findByText(/Expansion failed: boom/i)).toBeTruthy()
  })

  it('export buttons are absent before submit and present after', async () => {
    render(<ToolNetwork />, { wrapper: makeWrapper() })

    expect(screen.queryByRole('button', { name: 'Export JSON' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Export GraphML' })).toBeNull()

    vi.mocked(apiPost).mockResolvedValueOnce(SEED_RESULT)
    await submit()

    expect(await screen.findByRole('button', { name: 'Export JSON' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Export GraphML' })).toBeTruthy()
  })

  it('clicking Export JSON downloads a Blob without any backend call', async () => {
    const createObjectURL = vi.fn((_blob: Blob) => 'blob:mock-url')
    const revokeObjectURL = vi.fn()
    ;(globalThis as any).URL.createObjectURL = createObjectURL
    ;(globalThis as any).URL.revokeObjectURL = revokeObjectURL
    const clickSpy = vi.fn()
    const originalCreateElement = document.createElement.bind(document)
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      const el = originalCreateElement(tag)
      if (tag === 'a') (el as HTMLAnchorElement).click = clickSpy as () => void
      return el
    })

    vi.mocked(apiPost).mockResolvedValueOnce(SEED_RESULT)
    render(<ToolNetwork />, { wrapper: makeWrapper() })
    await submit()

    const callsBefore = vi.mocked(apiPost).mock.calls.length
    fireEvent.click(await screen.findByRole('button', { name: 'Export JSON' }))

    expect(createObjectURL).toHaveBeenCalledTimes(1)
    expect(createObjectURL.mock.calls[0][0]).toBeInstanceOf(Blob)
    expect(clickSpy).toHaveBeenCalledTimes(1)
    expect(vi.mocked(apiPost).mock.calls.length).toBe(callsBefore)

    vi.restoreAllMocks()
  })

  it('shows empty-state text and no svg for an empty seed result', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({
      seed: 'ghost',
      seed_node_id: null,
      nodes: [],
      edges: [],
      truncated: false,
    })

    const { container } = render(<ToolNetwork />, { wrapper: makeWrapper() })
    await submit('ghost')

    expect(await screen.findByText('no network — the entity matched nothing')).toBeTruthy()
    expect(container.querySelector('svg')).toBeNull()
  })
})
