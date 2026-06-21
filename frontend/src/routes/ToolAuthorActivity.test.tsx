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
import { ToolAuthorActivity } from './ToolAuthorActivity'

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

const SUMMARY_WITH_TOPICS = {
  author_id: 'user_001',
  handle: 'alice',
  display_name: 'Alice Smith',
  platform: 'TestNet',
  post_count: 10,
  posting_count: 7,
  comment_count: 2,
  message_count: 1,
  first_activity: '2024-01-01T10:00:00',
  last_activity: '2024-06-01T18:30:00',
  expected_reactions_total: 100,
  collected_reactions_total: 80,
  expected_comments_total: 50,
  collected_comments_total: 40,
  top_topics: [
    { topic: 'Climate', entity_id: 'e1', count: 5 },
    { topic: 'Politics', entity_id: null, count: 3 },
  ],
}

const SUMMARY_NO_TOPICS = {
  author_id: 'user_002',
  handle: 'bob_h',
  display_name: null,
  platform: 'TestNet',
  post_count: 3,
  posting_count: 3,
  comment_count: 0,
  message_count: 0,
  first_activity: '2024-03-01T09:00:00',
  last_activity: '2024-05-01T12:00:00',
  expected_reactions_total: 20,
  collected_reactions_total: 15,
  expected_comments_total: 10,
  collected_comments_total: 8,
  top_topics: [],
}

// ── tests ─────────────────────────────────────────────────────────────────────

describe('ToolAuthorActivity', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the title', async () => {
    render(<ToolAuthorActivity />, { wrapper: makeWrapper() })
    expect(await screen.findByText('author activity summary')).toBeTruthy()
  })

  it('submit button is disabled when author field is empty', async () => {
    render(<ToolAuthorActivity />, { wrapper: makeWrapper() })
    await screen.findByText('author activity summary')
    const btn = screen.getByRole('button', { name: /summarize/i })
    expect((btn as HTMLButtonElement).disabled).toBe(true)
  })

  it('submit button becomes enabled once author field is filled', async () => {
    render(<ToolAuthorActivity />, { wrapper: makeWrapper() })
    await screen.findByText('author activity summary')

    // author field is the first textbox (EntityInput; label is a sibling, not htmlFor)
    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'alice' } })

    const btn = screen.getByRole('button', { name: /summarize/i })
    expect((btn as HTMLButtonElement).disabled).toBe(false)
  })

  it('renders both summary card headers after submit', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({
      summaries: [SUMMARY_WITH_TOPICS, SUMMARY_NO_TOPICS],
    })

    render(<ToolAuthorActivity />, { wrapper: makeWrapper() })
    await screen.findByText('author activity summary')

    // author field is the first textbox (EntityInput; label is a sibling, not htmlFor)
    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'alice' } })
    fireEvent.click(screen.getByRole('button', { name: /summarize/i }))

    // First summary uses display_name as label
    expect(await screen.findByText('Alice Smith · user_001')).toBeTruthy()
    // Second summary uses handle (display_name is null)
    expect(screen.getByText('bob_h · user_002')).toBeTruthy()
  })

  it('renders top_topics DataTable for summary with topics', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({
      summaries: [SUMMARY_WITH_TOPICS, SUMMARY_NO_TOPICS],
    })

    render(<ToolAuthorActivity />, { wrapper: makeWrapper() })
    await screen.findByText('author activity summary')

    // author field is the first textbox (EntityInput; label is a sibling, not htmlFor)
    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'alice' } })
    fireEvent.click(screen.getByRole('button', { name: /summarize/i }))

    await screen.findByText('Alice Smith · user_001')
    // Topics table row content
    expect(screen.getByText('Climate')).toBeTruthy()
    expect(screen.getByText('Politics')).toBeTruthy()
  })

  it('renders "no topics" line for summary with empty top_topics', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({
      summaries: [SUMMARY_WITH_TOPICS, SUMMARY_NO_TOPICS],
    })

    render(<ToolAuthorActivity />, { wrapper: makeWrapper() })
    await screen.findByText('author activity summary')

    // author field is the first textbox (EntityInput; label is a sibling, not htmlFor)
    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'alice' } })
    fireEvent.click(screen.getByRole('button', { name: /summarize/i }))

    await screen.findByText('bob_h · user_002')
    expect(screen.getByText('no topics mentioned in range')).toBeTruthy()
  })

  it('renders matched count', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({
      summaries: [SUMMARY_WITH_TOPICS, SUMMARY_NO_TOPICS],
    })

    render(<ToolAuthorActivity />, { wrapper: makeWrapper() })
    await screen.findByText('author activity summary')

    // author field is the first textbox (EntityInput; label is a sibling, not htmlFor)
    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'alice' } })
    fireEvent.click(screen.getByRole('button', { name: /summarize/i }))

    expect(await screen.findByText('2 author(s) matched')).toBeTruthy()
  })

  it('renders "no matching author" when summaries is empty', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({ summaries: [] })

    render(<ToolAuthorActivity />, { wrapper: makeWrapper() })
    await screen.findByText('author activity summary')

    // author field is the first textbox (EntityInput; label is a sibling, not htmlFor)
    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'ghost' } })
    fireEvent.click(screen.getByRole('button', { name: /summarize/i }))

    expect(await screen.findByText('no matching author')).toBeTruthy()
  })

  it('shows a danger Banner on API error', async () => {
    vi.mocked(apiPost).mockRejectedValueOnce(new Error('server error'))

    render(<ToolAuthorActivity />, { wrapper: makeWrapper() })
    await screen.findByText('author activity summary')

    // author field is the first textbox (EntityInput; label is a sibling, not htmlFor)
    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'alice' } })
    fireEvent.click(screen.getByRole('button', { name: /summarize/i }))

    const banner = await screen.findByRole('alert')
    expect(banner).toBeTruthy()
  })

  it('POSTs payload with author, omitting empty from/to', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({ summaries: [] })

    render(<ToolAuthorActivity />, { wrapper: makeWrapper() })
    await screen.findByText('author activity summary')

    // author field is the first textbox (EntityInput; label is a sibling, not htmlFor)
    const input = screen.getAllByRole('textbox')[0]
    fireEvent.change(input, { target: { value: 'alice' } })
    fireEvent.click(screen.getByRole('button', { name: /summarize/i }))

    await waitFor(() => expect(apiPost).toHaveBeenCalled())
    const [[, payload]] = vi.mocked(apiPost).mock.calls
    expect(payload).toMatchObject({ author: 'alice' })
    expect(payload).not.toHaveProperty('from')
    expect(payload).not.toHaveProperty('to')
  })

  it('includes from/to in payload when provided', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({ summaries: [] })

    render(<ToolAuthorActivity />, { wrapper: makeWrapper() })
    await screen.findByText('author activity summary')

    const inputs = screen.getAllByRole('textbox')
    // author input is first (labeled), then from and to
    fireEvent.change(inputs[0], { target: { value: 'alice' } })
    // Find from/to by placeholder or index — TimeRangeInputs renders 2 more
    fireEvent.change(inputs[1], { target: { value: '2024-01-01T00:00:00Z' } })
    fireEvent.change(inputs[2], { target: { value: '2024-12-31T23:59:59Z' } })

    fireEvent.click(screen.getByRole('button', { name: /summarize/i }))

    await waitFor(() => expect(apiPost).toHaveBeenCalled())
    const [[, payload]] = vi.mocked(apiPost).mock.calls
    expect(payload).toMatchObject({
      author: 'alice',
      from: '2024-01-01T00:00:00Z',
      to: '2024-12-31T23:59:59Z',
    })
  })
})
