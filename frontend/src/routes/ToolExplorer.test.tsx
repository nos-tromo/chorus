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
import { ToolExplorer } from './ToolExplorer'

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

async function submitEntity(entity = 'Climate') {
  await screen.findByText('graph explorer')
  const input = screen.getAllByRole('textbox')[0]
  fireEvent.change(input, { target: { value: entity } })
  fireEvent.click(screen.getByRole('button', { name: /build graph/i }))
}

async function submitAuthor(author = 'Alice') {
  await screen.findByText('graph explorer')
  fireEvent.click(screen.getByRole('button', { name: 'Author' }))
  const input = screen.getAllByRole('textbox')[0]
  fireEvent.change(input, { target: { value: author } })
  fireEvent.click(screen.getByRole('button', { name: /build graph/i }))
}

// ── fixture data ──────────────────────────────────────────────────────────────

const NETWORK_SEED = {
  seed: 'Climate',
  seed_node_id: 'entity:e1',
  nodes: [
    { id: 'entity:e1', kind: 'topic', label: 'Climate', entity_id: 'e1', is_seed: true },
    { id: 'author:u1', kind: 'author', label: 'Alice', entity_id: null, is_seed: false },
  ],
  edges: [{ source: 'author:u1', target: 'entity:e1', weight: 3 }],
  truncated: false,
}

const SOCIAL_SEED = {
  seed: 'Alice',
  seed_node_id: 'author:a1',
  nodes: [
    { id: 'author:a1', label: 'Alice', ring: 0, is_seed: true },
    { id: 'author:a2', label: 'Bob', ring: 1, is_seed: false },
  ],
  edges: [{ source: 'author:a1', target: 'author:a2', kind: 'follows', directed: true }],
  truncated: false,
}

const EXPAND_TIES_RESULT = {
  nodes: [{ id: 'author:u2', label: 'Carol' }],
  edges: [{ source: 'author:u1', target: 'author:u2', kind: 'follows', directed: true }],
  truncated: false,
}

const EXPAND_MENTIONS_RESULT = {
  nodes: [{ id: 'author:u3', kind: 'author', label: 'Dave', entity_id: null, is_seed: false }],
  edges: [{ source: 'entity:e1', target: 'author:u3', weight: 1 }],
  truncated: false,
}

describe('ToolExplorer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('seed-type switch swaps the form fields', async () => {
    render(<ToolExplorer />, { wrapper: makeWrapper() })
    await screen.findByText('graph explorer')

    expect(screen.getByText(/topic limit/i)).toBeTruthy()
    expect(screen.queryByText(/second-ring limit/i)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Author' }))

    expect(screen.queryByText(/topic limit/i)).toBeNull()
    expect(screen.getByText(/second-ring limit/i)).toBeTruthy()
  })

  it('entity submit calls network_around and renders one node per graph node', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce(NETWORK_SEED)

    const { container } = render(<ToolExplorer />, { wrapper: makeWrapper() })
    await submitEntity()

    await waitFor(() => expect(container.querySelectorAll('g[role="button"]')).toHaveLength(2))
    const call = vi.mocked(apiPost).mock.calls.find(([path]) => path === '/tools/network_around')
    expect(call).toBeTruthy()
  })

  it('author submit calls social_network_around', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce(SOCIAL_SEED)

    const { container } = render(<ToolExplorer />, { wrapper: makeWrapper() })
    await submitAuthor()

    await waitFor(() => expect(container.querySelectorAll('g[role="button"]')).toHaveLength(2))
    const call = vi
      .mocked(apiPost)
      .mock.calls.find(([path]) => path === '/tools/social_network_around')
    expect(call).toBeTruthy()
  })

  it('selecting an author node shows both expand actions and each fires the right tool', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce(SOCIAL_SEED)
    vi.mocked(apiPost).mockResolvedValueOnce(EXPAND_TIES_RESULT)

    const { container } = render(<ToolExplorer />, { wrapper: makeWrapper() })
    await submitAuthor()
    await waitFor(() => expect(container.querySelectorAll('g[role="button"]')).toHaveLength(2))

    fireEvent.click(screen.getByRole('button', { name: /Alice/ }))

    expect(await screen.findByRole('button', { name: 'Expand topics' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Expand ties' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Expand ties' }))

    await waitFor(() => expect(container.querySelectorAll('g[role="button"]')).toHaveLength(3))
    const expandCall = vi
      .mocked(apiPost)
      .mock.calls.find(([path]) => path === '/tools/expand_social_node')
    expect(expandCall).toBeTruthy()
    expect(expandCall?.[1]).toMatchObject({ author_id: 'a1', limit: 50 })
  })

  it('selecting a topic node shows exactly one expand action, calling expand_network_node', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce(NETWORK_SEED)
    vi.mocked(apiPost).mockResolvedValueOnce(EXPAND_MENTIONS_RESULT)

    const { container } = render(<ToolExplorer />, { wrapper: makeWrapper() })
    await submitEntity()
    await waitFor(() => expect(container.querySelectorAll('g[role="button"]')).toHaveLength(2))

    fireEvent.click(screen.getByRole('button', { name: /Climate/ }))

    expect(await screen.findByRole('button', { name: 'Expand mentions' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Expand topics' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Expand ties' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Expand mentions' }))

    await waitFor(() => expect(container.querySelectorAll('g[role="button"]')).toHaveLength(3))
    const expandCall = vi
      .mocked(apiPost)
      .mock.calls.find(([path]) => path === '/tools/expand_network_node')
    expect(expandCall).toBeTruthy()
    expect(expandCall?.[1]).toMatchObject({ node_id: 'entity:e1', limit: 50 })
  })

  it('cross-family expansion grows the same canvas (entity seed, then expand ties on an author)', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce(NETWORK_SEED)
    vi.mocked(apiPost).mockResolvedValueOnce(EXPAND_TIES_RESULT)

    const { container } = render(<ToolExplorer />, { wrapper: makeWrapper() })
    await submitEntity()
    await waitFor(() => expect(container.querySelectorAll('g[role="button"]')).toHaveLength(2))

    fireEvent.click(screen.getByRole('button', { name: /Alice/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Expand ties' }))

    await waitFor(() => expect(container.querySelectorAll('g[role="button"]')).toHaveLength(3))
  })

  it('selecting a node then clicking Remove node shrinks the node count', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce(NETWORK_SEED)

    const { container } = render(<ToolExplorer />, { wrapper: makeWrapper() })
    await submitEntity()
    await waitFor(() => expect(container.querySelectorAll('g[role="button"]')).toHaveLength(2))

    fireEvent.click(screen.getByRole('button', { name: /Alice/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove node' }))

    await waitFor(() => expect(container.querySelectorAll('g[role="button"]')).toHaveLength(1))
  })

  it('export buttons are absent before submit and present after', async () => {
    render(<ToolExplorer />, { wrapper: makeWrapper() })

    expect(screen.queryByRole('button', { name: 'Export JSON' })).toBeNull()

    vi.mocked(apiPost).mockResolvedValueOnce(NETWORK_SEED)
    await submitEntity()

    expect(await screen.findByRole('button', { name: 'Export JSON' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Export GraphML' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Export HTML' })).toBeTruthy()
  })

  it('keeps the seed-capped banner visible after toggling the segmented control without reseeding', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({ ...NETWORK_SEED, truncated: true })

    render(<ToolExplorer />, { wrapper: makeWrapper() })
    await submitEntity()

    expect(await screen.findByText(/capped view/i)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Author' }))

    expect(screen.getByText(/capped view/i)).toBeTruthy()
  })

  it('shows empty-state text and no svg for an empty seed result', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({
      seed: 'ghost',
      seed_node_id: null,
      nodes: [],
      edges: [],
      truncated: false,
    })

    const { container } = render(<ToolExplorer />, { wrapper: makeWrapper() })
    await submitEntity('ghost')

    expect(await screen.findByText(/no matches/i)).toBeTruthy()
    expect(container.querySelector('svg')).toBeNull()
  })
})
