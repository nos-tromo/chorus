import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { ConfigProvider } from '../config/ConfigContext'
import { Shell } from './Shell'
import type { AppConfig, Whoami } from '../api/types'

const whoami: Whoami = {
  username: 'alice',
  display_name: null,
  projects: ['default'],
  active_project: 'default',
}

const getWhoami = vi.fn((): Promise<Whoami> => Promise.resolve(whoami))

vi.mock('../api/config', () => ({
  fetchConfig: vi.fn(
    (): Promise<AppConfig> =>
      Promise.resolve({ language: 'en', ingestion_enabled: false, version: '1.2.3' }),
  ),
  getWhoami: (...args: []) => getWhoami(...args),
}))

// Shell is exercised on its own here, including the case where whoami fails
// — which ProjectProvider would otherwise intercept before Shell renders.
// Provider integration is covered in Sidebar.test.tsx.
vi.mock('../project/ProjectContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../project/ProjectContext')>()),
  useProject: () => ({ projects: ['default'], active: 'default', setActive: vi.fn() }),
}))

function renderShell() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ConfigProvider>
        <MemoryRouter>
          <Shell>
            <div>content</div>
          </Shell>
        </MemoryRouter>
      </ConfigProvider>
    </QueryClientProvider>,
  )
}

describe('Shell', () => {
  it('passes the config version through to AppShell', async () => {
    renderShell()
    expect(await screen.findByText('v1.2.3')).toBeInTheDocument()
  })

  it('renders exactly one app-title header row', async () => {
    renderShell()
    await screen.findByText('v1.2.3')
    // AppShell's own title span, plus no duplicate "chorus" heading in Sidebar.
    expect(screen.getAllByText('chorus')).toHaveLength(1)
  })

  it('falls back to username when display_name is absent', async () => {
    renderShell()
    expect(await screen.findByRole('button', { name: /alice/i })).toBeInTheDocument()
  })

  it('prefers display_name over username when the gateway sends X-Auth-Name', async () => {
    getWhoami.mockResolvedValueOnce({ ...whoami, display_name: 'Alice Example' })
    renderShell()
    expect(await screen.findByRole('button', { name: /Alice Example/i })).toBeInTheDocument()
  })

  it('omits the user block when whoami fails (no identity configured in dev)', async () => {
    getWhoami.mockRejectedValueOnce(new Error('401'))
    renderShell()
    await screen.findByText('v1.2.3')
    expect(screen.queryByRole('button', { name: /account/i })).not.toBeInTheDocument()
  })
})
