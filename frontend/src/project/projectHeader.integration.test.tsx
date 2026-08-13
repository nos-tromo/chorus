import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { useQuery } from '@tanstack/react-query'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider } from '../config/ConfigContext'
import { ProjectProvider, ProjectScoped } from './ProjectContext'
import { apiGet, setActiveProjectHeader } from '../api/client'
import type { Whoami } from '../api/types'

// Only the identity/config calls are stubbed; the real API client runs, so
// this exercises the actual header the provider installs.
const getWhoami = vi.fn<() => Promise<Whoami>>()

vi.mock('../api/config', () => ({
  fetchConfig: () => Promise.resolve({ language: 'en', ingestion_enabled: false, version: '1' }),
  getWhoami: () => getWhoami(),
}))

const fetchMock = vi.fn<typeof fetch>(async () => new Response('{}', { status: 200 }))

/** Stands in for any project-scoped screen: fires a request the moment it mounts. */
function EagerChild() {
  const { isSuccess } = useQuery({ queryKey: ['stats'], queryFn: () => apiGet('/stats') })
  return <span data-testid="child">{isSuccess ? 'loaded' : 'loading'}</span>
}

beforeEach(() => {
  localStorage.clear()
  getWhoami.mockReset()
  fetchMock.mockClear()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  setActiveProjectHeader(null)
})

describe('project header on child requests', () => {
  it('is already set by the time a child fires its first request', async () => {
    getWhoami.mockResolvedValue({
      username: 'alice',
      display_name: null,
      projects: ['alpha', 'beta'],
      active_project: 'beta',
    })

    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ConfigProvider>
          <ProjectProvider>
            <ProjectScoped>
              <EagerChild />
            </ProjectScoped>
          </ProjectProvider>
        </ConfigProvider>
      </QueryClientProvider>,
    )

    expect(await screen.findByText('loaded')).toBeInTheDocument()

    // Every request the child made carried the project — none went out unscoped.
    const statsCalls = fetchMock.mock.calls.filter(([input]) => String(input).includes('/stats'))
    expect(statsCalls.length).toBeGreaterThan(0)
    for (const [, init] of statsCalls) {
      expect(new Headers(init?.headers as HeadersInit | undefined).get('x-chorus-project')).toBe(
        'beta',
      )
    }
  })
})
