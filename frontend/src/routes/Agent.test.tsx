import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import type { AppConfig, AgentResponse } from '../api/types'

// ── mocks (must run before any component imports) ─────────────────────────────

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
import { Agent } from './Agent'

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

const SIMPLE_RESPONSE: AgentResponse = {
  answer: 'Alice has 42 posts.',
  trace: [
    {
      tool: 'author_activity_summary',
      arguments: { author: 'Alice' },
      error: null,
      result_count: 1,
      result: null,
    },
  ],
  truncated: false,
}

const TRUNCATED_RESPONSE: AgentResponse = {
  answer: 'Stopped early.',
  trace: [],
  truncated: true,
}

const MARKDOWN_RESPONSE: AgentResponse = {
  answer: '**Berlin** results\n\n| Author | Posts |\n| --- | --- |\n| Alice | 2 |',
  trace: [],
  truncated: false,
}

// ── tests ─────────────────────────────────────────────────────────────────────

describe('Agent', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders title and chat input', async () => {
    render(<Agent />, { wrapper: makeWrapper() })
    expect(await screen.findByText('chorus agent')).toBeTruthy()
    expect(screen.getByTestId('agent-input')).toBeTruthy()
  })

  it('type a question, send → assistant answer renders', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce(SIMPLE_RESPONSE)

    render(<Agent />, { wrapper: makeWrapper() })
    await screen.findByText('chorus agent')

    const input = screen.getByTestId('agent-input')
    fireEvent.change(input, { target: { value: 'How active is Alice?' } })
    fireEvent.submit(input.closest('form')!)

    // User bubble appears
    expect(await screen.findByText('How active is Alice?')).toBeTruthy()
    // Assistant answer appears
    expect(await screen.findByText('Alice has 42 posts.')).toBeTruthy()
  })

  it('trace entries render under the assistant bubble', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce(SIMPLE_RESPONSE)

    render(<Agent />, { wrapper: makeWrapper() })
    await screen.findByText('chorus agent')

    const input = screen.getByTestId('agent-input')
    fireEvent.change(input, { target: { value: 'Summarise Alice' } })
    fireEvent.submit(input.closest('form')!)

    // ToolTrace summary shows 1 tool call
    expect(await screen.findByText('Tool calls (1)')).toBeTruthy()
  })

  it('a second send includes the FULL prior history in the POST payload', async () => {
    const secondResponse: AgentResponse = {
      answer: 'Bob has 5 posts.',
      trace: [],
      truncated: false,
    }
    vi.mocked(apiPost)
      .mockResolvedValueOnce(SIMPLE_RESPONSE)
      .mockResolvedValueOnce(secondResponse)

    render(<Agent />, { wrapper: makeWrapper() })
    await screen.findByText('chorus agent')

    const input = screen.getByTestId('agent-input')

    // First send
    fireEvent.change(input, { target: { value: 'How active is Alice?' } })
    fireEvent.submit(input.closest('form')!)
    await screen.findByText('Alice has 42 posts.')

    // Second send
    fireEvent.change(input, { target: { value: 'What about Bob?' } })
    fireEvent.submit(input.closest('form')!)
    await screen.findByText('Bob has 5 posts.')

    // The second POST payload should contain the full 3-turn history
    const calls = vi.mocked(apiPost).mock.calls
    expect(calls).toHaveLength(2)
    const secondPayload = calls[1][1] as { messages: { role: string; content: string }[] }
    expect(secondPayload.messages).toHaveLength(3)
    expect(secondPayload.messages[0]).toMatchObject({ role: 'user', content: 'How active is Alice?' })
    expect(secondPayload.messages[1]).toMatchObject({ role: 'assistant', content: 'Alice has 42 posts.' })
    expect(secondPayload.messages[2]).toMatchObject({ role: 'user', content: 'What about Bob?' })
  })

  it('clear button empties the conversation', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce(SIMPLE_RESPONSE)

    render(<Agent />, { wrapper: makeWrapper() })
    await screen.findByText('chorus agent')

    const input = screen.getByTestId('agent-input')
    fireEvent.change(input, { target: { value: 'Hello?' } })
    fireEvent.submit(input.closest('form')!)
    await screen.findByText('Hello?')

    fireEvent.click(screen.getByRole('button', { name: /clear conversation/i }))

    await waitFor(() => {
      expect(screen.queryByText('Hello?')).toBeNull()
      expect(screen.queryByText('Alice has 42 posts.')).toBeNull()
    })
  })

  it('truncated:true response shows the truncation notice', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce(TRUNCATED_RESPONSE)

    render(<Agent />, { wrapper: makeWrapper() })
    await screen.findByText('chorus agent')

    const input = screen.getByTestId('agent-input')
    fireEvent.change(input, { target: { value: 'Complex query' } })
    fireEvent.submit(input.closest('form')!)

    expect(
      await screen.findByText(/Stopped at the tool-call limit/i),
    ).toBeTruthy()
  })

  it('shows error banner when the API call fails', async () => {
    vi.mocked(apiPost).mockRejectedValueOnce(new Error('connection refused'))

    render(<Agent />, { wrapper: makeWrapper() })
    await screen.findByText('chorus agent')

    const input = screen.getByTestId('agent-input')
    fireEvent.change(input, { target: { value: 'Will this fail?' } })
    fireEvent.submit(input.closest('form')!)

    const banner = await screen.findByRole('alert')
    expect(banner.textContent).toMatch(/connection refused/i)
  })

  it('assistant markdown: bold and GFM table render as HTML elements', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce(MARKDOWN_RESPONSE)

    const { container } = render(<Agent />, { wrapper: makeWrapper() })
    await screen.findByText('chorus agent')

    const input = screen.getByTestId('agent-input')
    fireEvent.change(input, { target: { value: 'Who posted about Berlin?' } })
    fireEvent.submit(input.closest('form')!)

    // Wait for the assistant bubble to appear
    await screen.findByTestId('assistant-bubble')

    // Bold text rendered as <strong>
    await waitFor(() => {
      expect(container.querySelector('strong')).toBeTruthy()
    })
    // GFM table rendered as <table>
    expect(container.querySelector('table')).toBeTruthy()
  })

  it('user turns are NOT rendered as markdown (plain text)', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce(SIMPLE_RESPONSE)

    render(<Agent />, { wrapper: makeWrapper() })
    await screen.findByText('chorus agent')

    const input = screen.getByTestId('agent-input')
    fireEvent.change(input, { target: { value: 'How active is Alice?' } })
    fireEvent.submit(input.closest('form')!)

    // User bubble appears and is a plain <p>
    const userBubble = await screen.findByTestId('user-bubble')
    expect(userBubble.querySelector('p')).toBeTruthy()
    // No markdown parsing in user bubble
    expect(userBubble.querySelector('strong')).toBeNull()
  })

  it('copy button appears on assistant bubble (not on user bubble)', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce(SIMPLE_RESPONSE)

    render(<Agent />, { wrapper: makeWrapper() })
    await screen.findByText('chorus agent')

    const input = screen.getByTestId('agent-input')
    fireEvent.change(input, { target: { value: 'How active is Alice?' } })
    fireEvent.submit(input.closest('form')!)

    await screen.findByTestId('assistant-bubble')

    // Copy button exists inside assistant bubble
    const assistantBubble = screen.getByTestId('assistant-bubble')
    expect(assistantBubble.querySelector('button[title="Copy"]')).toBeTruthy()

    // User bubble has no copy button
    const userBubble = screen.getByTestId('user-bubble')
    expect(userBubble.querySelector('button')).toBeNull()
  })

  it('send button is disabled while pending', async () => {
    // Never resolves, so mutation stays pending
    vi.mocked(apiPost).mockImplementation(() => new Promise(() => {}))

    render(<Agent />, { wrapper: makeWrapper() })
    await screen.findByText('chorus agent')

    const input = screen.getByTestId('agent-input')
    fireEvent.change(input, { target: { value: 'Slow query' } })
    fireEvent.submit(input.closest('form')!)

    await waitFor(() => {
      const btn = screen.getByRole('button', { name: /suchen|search/i })
      expect((btn as HTMLButtonElement).disabled).toBe(true)
    })
  })
})
