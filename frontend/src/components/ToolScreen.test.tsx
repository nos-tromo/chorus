import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import type { AppConfig } from '../api/types'
import { POSTS_MENTIONING } from '../tools/specs'

// ── mock api/client (must come before any component import) ──────────────────

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
import { ToolScreen } from './ToolScreen'

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

// ── tests ─────────────────────────────────────────────────────────────────────

describe('ToolScreen', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the title from the spec', async () => {
    render(
      <ToolScreen spec={POSTS_MENTIONING} />,
      { wrapper: makeWrapper() },
    )
    // ConfigProvider is async (fetches config); wait for the title to appear
    expect(await screen.findByText('posts mentioning an entity')).toBeTruthy()
  })

  it('submit button is disabled when required field is empty', async () => {
    render(
      <ToolScreen spec={POSTS_MENTIONING} />,
      { wrapper: makeWrapper() },
    )
    await screen.findByText('posts mentioning an entity')
    const btn = screen.getByRole('button', { name: /search/i })
    expect((btn as HTMLButtonElement).disabled).toBe(true)
  })

  it('submit button becomes enabled once required field is filled', async () => {
    render(
      <ToolScreen spec={POSTS_MENTIONING} />,
      { wrapper: makeWrapper() },
    )
    await screen.findByText('posts mentioning an entity')

    // EntityInput renders label+input as siblings; grab the first textbox (entity field)
    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'Alice' } })

    const btn = screen.getByRole('button', { name: /search/i })
    expect((btn as HTMLButtonElement).disabled).toBe(false)
  })

  it('POSTs to /tools/posts_mentioning with the right payload on submit', async () => {
    const mockResult = { hits: [{ uuid: 'abc', text: 'hello world', ts: '2024-01-01T00:00:00Z', labels: ['Post'], entity_id: null, matched_name: 'Alice' }] }
    vi.mocked(apiPost).mockResolvedValueOnce(mockResult)

    render(
      <ToolScreen spec={POSTS_MENTIONING} />,
      { wrapper: makeWrapper() },
    )
    await screen.findByText('posts mentioning an entity')

    // Fill required entity field (first textbox)
    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'Alice' } })

    // Submit
    const btn = screen.getByRole('button', { name: /search/i })
    fireEvent.click(btn)

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        '/tools/posts_mentioning',
        expect.objectContaining({ entity: 'Alice', limit: 50 }),
      )
    })

    // from/to must NOT be in the payload when empty
    const [[, payload]] = vi.mocked(apiPost).mock.calls
    expect(payload).not.toHaveProperty('from')
    expect(payload).not.toHaveProperty('to')
  })

  it('renders returned rows in the DataTable', async () => {
    const mockResult = {
      hits: [
        { uuid: 'abc', text: 'hello world', ts: '2024-01-01T00:00:00Z', labels: ['Post'], entity_id: null, matched_name: 'Alice' },
        { uuid: 'def', text: 'another post', ts: '2024-02-01T00:00:00Z', labels: ['Post'], entity_id: 'e1', matched_name: 'Alice' },
      ],
    }
    vi.mocked(apiPost).mockResolvedValueOnce(mockResult)

    render(
      <ToolScreen spec={POSTS_MENTIONING} />,
      { wrapper: makeWrapper() },
    )
    await screen.findByText('posts mentioning an entity')

    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'Alice' } })
    fireEvent.click(screen.getByRole('button', { name: /search/i }))

    // The table should render cell content from the returned rows
    expect(await screen.findByText('hello world')).toBeTruthy()
    expect(screen.getByText('another post')).toBeTruthy()
  })

  it('shows empty-state message when result array is empty', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({ hits: [] })

    render(
      <ToolScreen spec={POSTS_MENTIONING} />,
      { wrapper: makeWrapper() },
    )
    await screen.findByText('posts mentioning an entity')

    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'Alice' } })
    fireEvent.click(screen.getByRole('button', { name: /search/i }))

    expect(await screen.findByText('no hits')).toBeTruthy()
  })

  it('shows a danger Banner on API error', async () => {
    vi.mocked(apiPost).mockRejectedValueOnce(new Error('server error'))

    render(
      <ToolScreen spec={POSTS_MENTIONING} />,
      { wrapper: makeWrapper() },
    )
    await screen.findByText('posts mentioning an entity')

    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'Alice' } })
    fireEvent.click(screen.getByRole('button', { name: /search/i }))

    const banner = await screen.findByRole('alert')
    expect(banner).toBeTruthy()
  })

  it('omits from/to from payload when the fields are empty strings', async () => {
    const mockResult = { hits: [] }
    vi.mocked(apiPost).mockResolvedValueOnce(mockResult)

    render(
      <ToolScreen spec={POSTS_MENTIONING} />,
      { wrapper: makeWrapper() },
    )
    await screen.findByText('posts mentioning an entity')

    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'Bob' } })
    fireEvent.click(screen.getByRole('button', { name: /search/i }))

    await waitFor(() => expect(apiPost).toHaveBeenCalled())
    const [[, payload]] = vi.mocked(apiPost).mock.calls
    expect(payload).not.toHaveProperty('from')
    expect(payload).not.toHaveProperty('to')
  })
})
