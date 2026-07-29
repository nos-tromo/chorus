import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { ConfigProvider } from '../config/ConfigContext'
import { Shell } from './Shell'
import type { AppConfig } from '../api/types'

vi.mock('../api/config', () => ({
  fetchConfig: vi.fn(
    (): Promise<AppConfig> =>
      Promise.resolve({ language: 'en', ingestion_enabled: false, version: '1.2.3' }),
  ),
  // Shell renders Sidebar, which mounts VersionBadge — stub its fetch too.
  getVersion: vi.fn((): Promise<{ version: string }> => Promise.resolve({ version: '' })),
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
  it('passes the config version through to AppHeader', async () => {
    renderShell()
    expect(await screen.findByTestId('appheader-version')).toHaveTextContent('v1.2.3')
  })

  it('renders exactly one app-title header row', async () => {
    renderShell()
    await screen.findByTestId('appheader-version')
    // AppHeader's own title span, plus no duplicate "chorus" heading in Sidebar.
    expect(screen.getAllByText('chorus')).toHaveLength(1)
  })
})
