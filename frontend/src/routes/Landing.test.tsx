import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Landing } from './Landing'
import type { AppConfig, ToolsList } from '../api/types'

// ── mock api modules ─────────────────────────────────────────────────────────

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

import { apiGet } from '../api/client'
import { fetchConfig } from '../api/config'

// ── helpers ───────────────────────────────────────────────────────────────────

const MOCK_TOOLS: ToolsList = [
  {
    name: 'posts_mentioning',
    description: 'Get posts mentioning an entity',
    input_schema: {},
    output_schema: {},
  },
  {
    name: 'authors_mentioning',
    description: 'Get authors mentioning an entity',
    input_schema: {},
    output_schema: {},
  },
]

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

// Landing requires ConfigProvider (useConfig / useT) and QueryClientProvider (useHealth, useTools).
// Import lazily so vi.mock runs first.
import { ConfigProvider } from '../config/ConfigContext'

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={makeClient()}>
      <ConfigProvider>{children}</ConfigProvider>
    </QueryClientProvider>
  )
}

// ── tests ─────────────────────────────────────────────────────────────────────

describe('Landing', () => {
  it('(a) health-ok path: shows tool names from the tools list', async () => {
    vi.mocked(apiGet).mockImplementation((path: string) => {
      if (path === '/health') return Promise.resolve({ status: 'ok' })
      if (path === '/tools') return Promise.resolve(MOCK_TOOLS)
      return Promise.reject(new Error(`unexpected GET ${path}`))
    })

    render(
      <Wrapper>
        <Landing />
      </Wrapper>,
    )

    // tool names must appear
    expect(await screen.findByText('posts_mentioning')).toBeTruthy()
    expect(screen.getByText('authors_mentioning')).toBeTruthy()
  })

  it('(b) health-error path: shows a danger Banner', async () => {
    vi.mocked(apiGet).mockImplementation((path: string) => {
      if (path === '/health') return Promise.reject(new Error('connection refused'))
      if (path === '/tools') return Promise.resolve(MOCK_TOOLS)
      return Promise.reject(new Error(`unexpected GET ${path}`))
    })

    render(
      <Wrapper>
        <Landing />
      </Wrapper>,
    )

    // The danger banner has role="alert" and contains the error message
    const banner = await screen.findByRole('alert')
    expect(banner).toBeTruthy()
    expect(banner.textContent).toMatch(/connection refused/i)
  })

  it('(c) ingestion-status: shows ingestion-on text when ingestion_enabled=true', async () => {
    vi.mocked(fetchConfig).mockResolvedValueOnce({
      language: 'en',
      ingestion_enabled: true,
      version: '0.1.0',
    })
    vi.mocked(apiGet).mockImplementation((path: string) => {
      if (path === '/health') return Promise.resolve({ status: 'ok' })
      if (path === '/tools') return Promise.resolve(MOCK_TOOLS)
      return Promise.reject(new Error(`unexpected GET ${path}`))
    })

    render(
      <Wrapper>
        <Landing />
      </Wrapper>,
    )

    expect(
      await screen.findByText(/Data ingestion is enabled/i),
    ).toBeTruthy()
  })

  it('(c) ingestion-status: shows ingestion-off text when ingestion_enabled=false', async () => {
    vi.mocked(fetchConfig).mockResolvedValueOnce({
      language: 'en',
      ingestion_enabled: false,
      version: '0.1.0',
    })
    vi.mocked(apiGet).mockImplementation((path: string) => {
      if (path === '/health') return Promise.resolve({ status: 'ok' })
      if (path === '/tools') return Promise.resolve(MOCK_TOOLS)
      return Promise.reject(new Error(`unexpected GET ${path}`))
    })

    render(
      <Wrapper>
        <Landing />
      </Wrapper>,
    )

    expect(
      await screen.findByText(/Data ingestion is disabled/i),
    ).toBeTruthy()
  })
})
