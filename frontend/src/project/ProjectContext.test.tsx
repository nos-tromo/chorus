import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useEffect } from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider } from '../config/ConfigContext'
import { ProjectProvider, ProjectScoped, useProject, PROJECT_STORAGE_KEY } from './ProjectContext'
import { setActiveProjectHeader } from '../api/client'
import type { AppConfig, Whoami } from '../api/types'

const getWhoami = vi.fn<() => Promise<Whoami>>()

vi.mock('../api/config', () => ({
  fetchConfig: vi.fn(
    (): Promise<AppConfig> =>
      Promise.resolve({ language: 'en', ingestion_enabled: false, version: '1.2.3' }),
  ),
  getWhoami: () => getWhoami(),
}))

vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  setActiveProjectHeader: vi.fn(),
}))

const headerSpy = vi.mocked(setActiveProjectHeader)

function whoami(over: Partial<Whoami> = {}): Whoami {
  return {
    username: 'alice',
    display_name: null,
    projects: ['alpha', 'beta'],
    active_project: null,
    ...over,
  }
}

let mounts = 0

/** Reports the active project and counts its own mounts, so a switch that
 *  fails to remount the subtree shows up as a stalled counter. */
function Probe() {
  const { active, projects, setActive } = useProject()
  useEffect(() => {
    mounts += 1
  }, [])
  return (
    <div>
      <span data-testid="active">{active}</span>
      <span data-testid="projects">{projects.join(',')}</span>
      <button onClick={() => setActive('beta')}>switch to beta</button>
    </div>
  )
}

function renderProvider(qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  const utils = render(
    <QueryClientProvider client={qc}>
      <ConfigProvider>
        <ProjectProvider>
          <ProjectScoped>
            <Probe />
          </ProjectScoped>
        </ProjectProvider>
      </ConfigProvider>
    </QueryClientProvider>,
  )
  return { ...utils, qc }
}

beforeEach(() => {
  mounts = 0
  localStorage.clear()
  getWhoami.mockReset()
  headerSpy.mockClear()
})

describe('ProjectProvider bootstrap', () => {
  it('opens the only allowed project without prompting', async () => {
    getWhoami.mockResolvedValue(whoami({ projects: ['alpha'], active_project: 'alpha' }))
    renderProvider()

    expect((await screen.findByTestId('active')).textContent).toBe('alpha')
    expect(headerSpy).toHaveBeenCalledWith('alpha')
    expect(localStorage.getItem(PROJECT_STORAGE_KEY)).toBe('alpha')
  })

  it('opens the project the server already resolved', async () => {
    getWhoami.mockResolvedValue(whoami({ active_project: 'beta' }))
    renderProvider()

    expect((await screen.findByTestId('active')).textContent).toBe('beta')
  })

  it('prefers a remembered project over the one the server resolved', async () => {
    localStorage.setItem(PROJECT_STORAGE_KEY, 'beta')
    getWhoami.mockResolvedValue(whoami({ active_project: 'alpha' }))
    renderProvider()

    expect((await screen.findByTestId('active')).textContent).toBe('beta')
  })

  it('prompts when several projects are allowed and none resolved', async () => {
    getWhoami.mockResolvedValue(whoami())
    renderProvider()

    expect(await screen.findByRole('button', { name: 'alpha' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'beta' })).toBeInTheDocument()
    expect(screen.queryByTestId('active')).not.toBeInTheDocument()
    expect(headerSpy).not.toHaveBeenCalledWith(expect.any(String))
  })

  it('opens the project picked at the prompt', async () => {
    getWhoami.mockResolvedValue(whoami())
    renderProvider()

    fireEvent.click(await screen.findByRole('button', { name: 'beta' }))

    expect((await screen.findByTestId('active')).textContent).toBe('beta')
    expect(headerSpy).toHaveBeenCalledWith('beta')
  })

  it('forgets a remembered project that is no longer allowed', async () => {
    localStorage.setItem(PROJECT_STORAGE_KEY, 'ghost')
    getWhoami.mockResolvedValue(whoami())
    renderProvider()

    expect(await screen.findByRole('button', { name: 'alpha' })).toBeInTheDocument()
    expect(localStorage.getItem(PROJECT_STORAGE_KEY)).toBeNull()
  })

  it('reports a claim that grants no projects instead of rendering the app', async () => {
    getWhoami.mockResolvedValue(whoami({ projects: [], active_project: null }))
    renderProvider()

    expect(await screen.findByText(/no project access/i)).toBeInTheDocument()
    expect(screen.queryByTestId('active')).not.toBeInTheDocument()
  })

  it('reports an unreachable backend as such, not as a missing claim', async () => {
    getWhoami.mockRejectedValue(new TypeError('fetch failed'))
    renderProvider()

    expect(await screen.findByText(/service unreachable/i)).toBeInTheDocument()
    expect(screen.queryByText(/no project access/i)).not.toBeInTheDocument()
  })
})

describe('switching projects', () => {
  it('drops project-scoped cache entries but keeps the session ones', async () => {
    getWhoami.mockResolvedValue(whoami({ active_project: 'alpha' }))
    const { qc } = renderProvider()
    await screen.findByTestId('active')

    qc.setQueryData(['stats'], { counts: 1 })
    qc.setQueryData(['tools'], [{ name: 'posts_mentioning' }])
    qc.setQueryData(['job', 'abc'], { id: 'abc' })

    fireEvent.click(screen.getByRole('button', { name: /switch to beta/i }))

    expect(qc.getQueryData(['stats'])).toBeUndefined()
    expect(qc.getQueryData(['tools'])).toBeUndefined()
    expect(qc.getQueryData(['job', 'abc'])).toBeUndefined()
    expect(qc.getQueryData(['whoami'])).toBeDefined()
    expect(qc.getQueryData(['config'])).toBeDefined()
  })

  it('remounts the project-scoped subtree so stale graph state cannot survive', async () => {
    getWhoami.mockResolvedValue(whoami({ active_project: 'alpha' }))
    renderProvider()
    await screen.findByTestId('active')
    const before = mounts

    fireEvent.click(screen.getByRole('button', { name: /switch to beta/i }))

    expect((await screen.findByTestId('active')).textContent).toBe('beta')
    expect(mounts).toBeGreaterThan(before)
  })

  it('sends the new project on subsequent requests and remembers it', async () => {
    getWhoami.mockResolvedValue(whoami({ active_project: 'alpha' }))
    renderProvider()
    await screen.findByTestId('active')

    fireEvent.click(screen.getByRole('button', { name: /switch to beta/i }))

    expect((await screen.findByTestId('active')).textContent).toBe('beta')
    expect(headerSpy).toHaveBeenLastCalledWith('beta')
    expect(localStorage.getItem(PROJECT_STORAGE_KEY)).toBe('beta')
  })
})
