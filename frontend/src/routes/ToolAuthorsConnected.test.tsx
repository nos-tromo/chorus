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
import { ToolAuthorsConnected } from './ToolAuthorsConnected'

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

// ── fixture data ──────────────────────────────────────────────────────────────

const GROUP_WITH_CONNECTED = {
  seed: { author_id: 'u_001', handle: 'alice_h', display_name: 'Alice Smith' },
  connected: [
    {
      author_id: 'u_002',
      handle: 'bob_h',
      display_name: 'Bob Jones',
      overlap: 3,
      shared_topics: ['Climate', 'Politics', 'Tech'],
    },
    {
      author_id: 'u_003',
      handle: null,
      display_name: 'Carol White',
      overlap: 1,
      shared_topics: ['Climate'],
    },
  ],
}

const GROUP_NO_CONNECTED = {
  seed: { author_id: 'u_004', handle: 'dave_h', display_name: null },
  connected: [],
}

// ── tests ─────────────────────────────────────────────────────────────────────

describe('ToolAuthorsConnected', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the title', async () => {
    render(<ToolAuthorsConnected />, { wrapper: makeWrapper() })
    expect(await screen.findByText('authors connected by topic')).toBeTruthy()
  })

  it('submit button is disabled when seed_author field is empty', async () => {
    render(<ToolAuthorsConnected />, { wrapper: makeWrapper() })
    await screen.findByText('authors connected by topic')
    const btn = screen.getByRole('button', { name: /find connected authors/i })
    expect((btn as HTMLButtonElement).disabled).toBe(true)
  })

  it('submit button becomes enabled when seed_author is filled', async () => {
    render(<ToolAuthorsConnected />, { wrapper: makeWrapper() })
    await screen.findByText('authors connected by topic')

    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'alice' } })

    const btn = screen.getByRole('button', { name: /find connected authors/i })
    expect((btn as HTMLButtonElement).disabled).toBe(false)
  })

  it('renders both group headers with correct label and count', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({
      results: [GROUP_WITH_CONNECTED, GROUP_NO_CONNECTED],
    })

    render(<ToolAuthorsConnected />, { wrapper: makeWrapper() })
    await screen.findByText('authors connected by topic')

    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'alice' } })
    fireEvent.click(screen.getByRole('button', { name: /find connected authors/i }))

    // First group: display_name is used as label, 2 connected
    // Note: findByText normalizes whitespace, so match with regex
    expect(await screen.findByText(/Alice Smith\s+·\s+2 connected/)).toBeTruthy()
    // Second group: handle used (display_name is null), 0 connected
    expect(screen.getByText(/dave_h\s+·\s+0 connected/)).toBeTruthy()
  })

  it('renders the connected DataTable for a group with authors', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({
      results: [GROUP_WITH_CONNECTED, GROUP_NO_CONNECTED],
    })

    render(<ToolAuthorsConnected />, { wrapper: makeWrapper() })
    await screen.findByText('authors connected by topic')

    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'alice' } })
    fireEvent.click(screen.getByRole('button', { name: /find connected authors/i }))

    await screen.findByText(/Alice Smith\s+·\s+2 connected/)
    // Connected author data should appear in the table
    expect(screen.getByText('Bob Jones')).toBeTruthy()
    expect(screen.getByText('Carol White')).toBeTruthy()
  })

  it('renders the "none" line for a group with no connected authors', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({
      results: [GROUP_WITH_CONNECTED, GROUP_NO_CONNECTED],
    })

    render(<ToolAuthorsConnected />, { wrapper: makeWrapper() })
    await screen.findByText('authors connected by topic')

    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'alice' } })
    fireEvent.click(screen.getByRole('button', { name: /find connected authors/i }))

    await screen.findByText(/dave_h\s+·\s+0 connected/)
    expect(
      screen.getByText('no connected authors at this overlap threshold'),
    ).toBeTruthy()
  })

  it('renders "no matching seed author" when results is empty', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({ results: [] })

    render(<ToolAuthorsConnected />, { wrapper: makeWrapper() })
    await screen.findByText('authors connected by topic')

    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'ghost' } })
    fireEvent.click(screen.getByRole('button', { name: /find connected authors/i }))

    expect(await screen.findByText('no matching seed author')).toBeTruthy()
  })

  it('shows a danger Banner on API error', async () => {
    vi.mocked(apiPost).mockRejectedValueOnce(new Error('server error'))

    render(<ToolAuthorsConnected />, { wrapper: makeWrapper() })
    await screen.findByText('authors connected by topic')

    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'alice' } })
    fireEvent.click(screen.getByRole('button', { name: /find connected authors/i }))

    const banner = await screen.findByRole('alert')
    expect(banner).toBeTruthy()
  })

  it('POSTs exactly { seed_author, min_overlap, limit } — no max_hops, no from/to', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({ results: [] })

    render(<ToolAuthorsConnected />, { wrapper: makeWrapper() })
    await screen.findByText('authors connected by topic')

    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'alice' } })
    fireEvent.click(screen.getByRole('button', { name: /find connected authors/i }))

    await waitFor(() => expect(apiPost).toHaveBeenCalled())
    const [[, payload]] = vi.mocked(apiPost).mock.calls
    // must contain exactly these three keys
    expect(payload).toMatchObject({ seed_author: 'alice', min_overlap: 1, limit: 50 })
    expect(payload).not.toHaveProperty('max_hops')
    expect(payload).not.toHaveProperty('from')
    expect(payload).not.toHaveProperty('to')
    // verify no extra keys
    expect(Object.keys(payload as object)).toHaveLength(3)
  })
})
