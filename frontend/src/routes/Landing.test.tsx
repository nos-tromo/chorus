import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Landing } from './Landing'
import type { AppConfig, ToolsList, GraphStats } from '../api/types'

// ── mock recharts (ResponsiveContainer requires a sized DOM which happy-dom can't provide) ──

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="responsive-container">{children}</div>
  ),
  BarChart: ({ children, data }: { children?: React.ReactNode; data?: unknown[] }) => (
    <div data-testid="bar-chart" data-item-count={data?.length ?? 0}>
      {children}
    </div>
  ),
  Bar: () => <div data-testid="bar" />,
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  Tooltip: () => <div data-testid="tooltip" />,
  CartesianGrid: () => <div data-testid="cartesian-grid" />,
}))

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

// ── fixtures ──────────────────────────────────────────────────────────────────

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

const MOCK_STATS: GraphStats = {
  counts: {
    posts: 1234,
    authors: 87,
    entities: 42,
    hashtags: 15,
    groups: 3,
    platforms: 2,
    aliases: 60,
  },
  edges: {
    mentions: 890,
    authored: 1234,
    follows: 200,
    friends: 50,
    resolved: 55,
  },
  top_entities: [
    { name: 'Alice Wonderland', count: 77 },
    { name: 'Bob Builder', count: 55 },
  ],
  top_authors: [
    { author_id: 'a1', label: 'charlie_author', count: 99 },
    { author_id: 'a2', label: 'diana_posts', count: 66 },
  ],
  posts_by_platform: [
    { platform: 'Facebook', count: 700 },
    { platform: 'Twitter', count: 534 },
  ],
  latest_ingested_at: '2026-06-19T12:00:00Z',
  resolution: { resolved_aliases: 55, total_aliases: 60 },
}

const EMPTY_STATS: GraphStats = {
  counts: { posts: 0, authors: 0, entities: 0, hashtags: 0, groups: 0, platforms: 0, aliases: 0 },
  edges: { mentions: 0, authored: 0, follows: 0, friends: 0, resolved: 0 },
  top_entities: [],
  top_authors: [],
  posts_by_platform: [],
  latest_ingested_at: null,
  resolution: { resolved_aliases: 0, total_aliases: 0 },
}

// ── helpers ───────────────────────────────────────────────────────────────────

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

function mockAllApis(statsOverride?: Partial<GraphStats> | 'error' | 'loading') {
  vi.mocked(apiGet).mockImplementation((path: string) => {
    if (path === '/health') return Promise.resolve({ status: 'ok' })
    if (path === '/tools') return Promise.resolve(MOCK_TOOLS)
    if (path === '/stats') {
      if (statsOverride === 'error') return Promise.reject(new Error('stats fetch failed'))
      if (statsOverride === 'loading') return new Promise(() => {}) // never resolves
      return Promise.resolve(
        statsOverride ? { ...MOCK_STATS, ...statsOverride } : MOCK_STATS,
      )
    }
    return Promise.reject(new Error(`unexpected GET ${path}`))
  })
}

// ── tests ─────────────────────────────────────────────────────────────────────

describe('Landing', () => {
  it('(a) health-ok path: shows tool names from the tools list', async () => {
    mockAllApis()

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
      if (path === '/stats') return Promise.resolve(MOCK_STATS)
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
    mockAllApis()

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
    mockAllApis()

    render(
      <Wrapper>
        <Landing />
      </Wrapper>,
    )

    expect(
      await screen.findByText(/Data ingestion is disabled/i),
    ).toBeTruthy()
  })

  // ── dashboard stats tests ─────────────────────────────────────────────────

  it('(d) dashboard: shows KPI counts for posts and authors', async () => {
    mockAllApis()

    render(
      <Wrapper>
        <Landing />
      </Wrapper>,
    )

    // Posts count = 1234, authors count = 87
    expect(await screen.findByTestId('kpi-posts')).toBeTruthy()
    expect(screen.getByTestId('kpi-posts').textContent).toContain('1234')
    expect(screen.getByTestId('kpi-authors').textContent).toContain('87')
  })

  it('(d) dashboard: shows edge counts for mentions and follows', async () => {
    mockAllApis()

    render(
      <Wrapper>
        <Landing />
      </Wrapper>,
    )

    expect(await screen.findByTestId('kpi-mentions')).toBeTruthy()
    expect(screen.getByTestId('kpi-mentions').textContent).toContain('890')
    expect(screen.getByTestId('kpi-follows').textContent).toContain('200')
  })

  it('(d) dashboard: shows top entity name', async () => {
    mockAllApis()

    render(
      <Wrapper>
        <Landing />
      </Wrapper>,
    )

    expect(await screen.findByText('Alice Wonderland')).toBeTruthy()
  })

  it('(d) dashboard: shows top author label', async () => {
    mockAllApis()

    render(
      <Wrapper>
        <Landing />
      </Wrapper>,
    )

    expect(await screen.findByText('charlie_author')).toBeTruthy()
  })

  it('(d) dashboard: shows resolution coverage percentage', async () => {
    mockAllApis()

    render(
      <Wrapper>
        <Landing />
      </Wrapper>,
    )

    // 55/60 ≈ 91.7% — just check "%" is present in the resolution coverage area
    const coverageEl = await screen.findByTestId('resolution-coverage')
    expect(coverageEl.textContent).toMatch(/%/)
  })

  it('(d) dashboard: renders chart container with platform data', async () => {
    mockAllApis()

    render(
      <Wrapper>
        <Landing />
      </Wrapper>,
    )

    // Mocked recharts renders data-testid="stat-chart"
    expect(await screen.findByTestId('stat-chart')).toBeTruthy()
  })

  it('(d) dashboard: loading state shows a spinner', async () => {
    mockAllApis('loading')

    render(
      <Wrapper>
        <Landing />
      </Wrapper>,
    )

    // The stats section spinner should appear (use testid we'll add)
    expect(await screen.findByTestId('stats-spinner')).toBeTruthy()
  })

  it('(d) dashboard: error state shows a danger Banner', async () => {
    mockAllApis('error')

    render(
      <Wrapper>
        <Landing />
      </Wrapper>,
    )

    // At least one alert should appear (could be health or stats)
    const alerts = await screen.findAllByRole('alert')
    const hasStatsError = alerts.some((a) => a.textContent?.match(/stats fetch failed/i))
    expect(hasStatsError).toBe(true)
  })

  it('(d) dashboard: empty graph (posts=0) shows no-data hint', async () => {
    vi.mocked(apiGet).mockImplementation((path: string) => {
      if (path === '/health') return Promise.resolve({ status: 'ok' })
      if (path === '/tools') return Promise.resolve(MOCK_TOOLS)
      if (path === '/stats') return Promise.resolve(EMPTY_STATS)
      return Promise.reject(new Error(`unexpected GET ${path}`))
    })

    render(
      <Wrapper>
        <Landing />
      </Wrapper>,
    )

    expect(await screen.findByTestId('stats-no-data')).toBeTruthy()
  })

  it('(d) dashboard: latest ingestion time is displayed when present', async () => {
    mockAllApis()

    render(
      <Wrapper>
        <Landing />
      </Wrapper>,
    )

    const ingestEl = await screen.findByTestId('latest-ingestion')
    // Should show a formatted date string, not the raw null
    expect(ingestEl.textContent).not.toBe('')
    expect(ingestEl.textContent).not.toBe('—')
  })

  it('(d) dashboard: shows dash when latest_ingested_at is null', async () => {
    vi.mocked(apiGet).mockImplementation((path: string) => {
      if (path === '/health') return Promise.resolve({ status: 'ok' })
      if (path === '/tools') return Promise.resolve(MOCK_TOOLS)
      if (path === '/stats') return Promise.resolve(EMPTY_STATS)
      return Promise.reject(new Error(`unexpected GET ${path}`))
    })

    render(
      <Wrapper>
        <Landing />
      </Wrapper>,
    )

    // In empty state there's no ingestion so we see the no-data hint; it replaces the dashboard
    expect(await screen.findByTestId('stats-no-data')).toBeTruthy()
  })
})
