import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { AppConfig } from '../api/types'

// ── mocks (must run before any component imports) ─────────────────────────────

vi.mock('../api/config', () => ({
  fetchConfig: vi.fn(
    (): Promise<AppConfig> =>
      Promise.resolve({ language: 'en', ingestion_enabled: false, version: '0.1.0' }),
  ),
}))

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

import { ConfigProvider } from '../config/ConfigContext'
import { ToolTrace } from './ToolTrace'
import type { AgentTraceEntry } from '../api/types'

// ── helpers ───────────────────────────────────────────────────────────────────

function makeWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <ConfigProvider>{children}</ConfigProvider>
      </QueryClientProvider>
    )
  }
}

// ── tests ─────────────────────────────────────────────────────────────────────

describe('ToolTrace', () => {
  it('renders nothing when trace is empty', async () => {
    render(<ToolTrace trace={[]} />, { wrapper: makeWrapper() })
    // Wait for ConfigProvider to finish loading, then assert nothing from ToolTrace
    // is rendered. The component returns null for empty trace, so there should be
    // no <details> element.
    await waitFor(() => {
      expect(document.querySelector('details')).toBeNull()
    })
  })

  it('renders the tool call count in the summary', async () => {
    const trace: AgentTraceEntry[] = [
      { tool: 'posts_mentioning', arguments: { entity: 'Alice' }, error: null, result_count: 3 },
    ]
    render(<ToolTrace trace={trace} />, { wrapper: makeWrapper() })
    expect(await screen.findByText('Tool calls (1)')).toBeTruthy()
  })

  it('renders tool name for a successful entry', async () => {
    const trace: AgentTraceEntry[] = [
      { tool: 'posts_mentioning', arguments: { entity: 'Alice' }, error: null, result_count: 3 },
    ]
    render(<ToolTrace trace={trace} />, { wrapper: makeWrapper() })
    expect(await screen.findByText('posts_mentioning')).toBeTruthy()
  })

  it('renders result_count when present', async () => {
    const trace: AgentTraceEntry[] = [
      { tool: 'posts_mentioning', arguments: { entity: 'Alice' }, error: null, result_count: 7 },
    ]
    render(<ToolTrace trace={trace} />, { wrapper: makeWrapper() })
    // i18n: 'agent.trace_results' = ' — {count} result(s)'
    // The span text starts with a space that Testing Library normalizes away,
    // so use a function matcher to check trimmed text content.
    expect(
      await screen.findByText((content) => content.includes('7 result(s)')),
    ).toBeTruthy()
  })

  it('does not render result suffix when result_count is null', async () => {
    const trace: AgentTraceEntry[] = [
      { tool: 'authors_mentioning', arguments: {}, error: null, result_count: null },
    ]
    render(<ToolTrace trace={trace} />, { wrapper: makeWrapper() })
    await screen.findByText('authors_mentioning')
    expect(screen.queryByText(/result/i)).toBeNull()
  })

  it('renders error entry with tool name and error message', async () => {
    const trace: AgentTraceEntry[] = [
      {
        tool: 'topic_co_occurrence',
        arguments: { entity: 'X', hops: 1 },
        error: 'entity not found',
        result_count: null,
      },
    ]
    render(<ToolTrace trace={trace} />, { wrapper: makeWrapper() })
    // tool name appears (in the <strong>)
    expect(await screen.findByText('topic_co_occurrence')).toBeTruthy()
    // error text appears
    expect(screen.getByText(/entity not found/)).toBeTruthy()
  })

  it('renders serialised arguments as JSON', async () => {
    const trace: AgentTraceEntry[] = [
      { tool: 'posts_mentioning', arguments: { entity: 'Alice', limit: 10 }, error: null, result_count: 2 },
    ]
    render(<ToolTrace trace={trace} />, { wrapper: makeWrapper() })
    await screen.findByText('posts_mentioning')
    const pre = document.querySelector('pre')
    expect(pre?.textContent).toContain('"Alice"')
    expect(pre?.textContent).toContain('"limit"')
  })

  it('renders multiple trace entries', async () => {
    const trace: AgentTraceEntry[] = [
      { tool: 'posts_mentioning', arguments: {}, error: null, result_count: 5 },
      { tool: 'authors_mentioning', arguments: {}, error: null, result_count: 2 },
    ]
    render(<ToolTrace trace={trace} />, { wrapper: makeWrapper() })
    expect(await screen.findByText('Tool calls (2)')).toBeTruthy()
    expect(screen.getByText('posts_mentioning')).toBeTruthy()
    expect(screen.getByText('authors_mentioning')).toBeTruthy()
  })
})
