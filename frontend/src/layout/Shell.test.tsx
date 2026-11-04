import { afterEach, describe, it, expect, vi } from 'vitest'
import { useState, type ReactNode } from 'react'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { PURPOSE_HEADER, clearAllPurposes } from '@infra/ui'
import { apiGet } from '../api/client'
import { ConfigProvider } from '../config/ConfigContext'
import { Shell } from './Shell'
import type { AppConfig, Whoami } from '../api/types'

const CONFIG: AppConfig = { language: 'en', ingestion_enabled: false, version: '1.2.3', require_purpose: [] }

const fetchConfig = vi.fn((): Promise<AppConfig> => Promise.resolve(CONFIG))
const getWhoami = vi.fn((): Promise<Whoami> => Promise.resolve({ username: 'alice', display_name: null }))

vi.mock('../api/config', () => ({
  fetchConfig: (...args: []) => fetchConfig(...args),
  getWhoami: (...args: []) => getWhoami(...args),
}))

afterEach(() => {
  clearAllPurposes()
  vi.unstubAllGlobals()
})

function renderShell(children: ReactNode = <div>content</div>) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ConfigProvider>
        <MemoryRouter>
          <Shell>{children}</Shell>
        </MemoryRouter>
      </ConfigProvider>
    </QueryClientProvider>,
  )
}

/** Queries /stats through the app's client when clicked and shows the result. */
function StatsProbe() {
  const [out, setOut] = useState('')
  return (
    <button type="button" onClick={() => void apiGet<{ ok: number }>('/stats').then((r) => setOut(String(r.ok)))}>
      probe {out}
    </button>
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
    getWhoami.mockResolvedValueOnce({ username: 'alice', display_name: 'Alice Example' })
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

describe('Shell purpose bar', () => {
  it('is in the header, not required when /config lists no action', async () => {
    renderShell()
    const bar = await screen.findByRole('button', { name: 'No purpose stated' })
    expect(bar).toHaveAttribute('data-state', 'empty')
    expect(within(screen.getByRole('banner')).getByRole('button', { name: 'No purpose stated' })).toBe(bar)
  })

  it('is required when /config lists actions that need a purpose', async () => {
    fetchConfig.mockResolvedValueOnce({ ...CONFIG, require_purpose: ['disclose', 'query'] })
    renderShell()
    const bar = await screen.findByRole('button', { name: 'Purpose required' })
    expect(bar).toHaveAttribute('data-state', 'required')
  })

  it('speaks German when the UI does', async () => {
    fetchConfig.mockResolvedValueOnce({ ...CONFIG, language: 'de', require_purpose: ['query'] })
    renderShell()
    fireEvent.click(await screen.findByRole('button', { name: 'Zweck erforderlich' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByLabelText('Aktenzeichen')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Begründung')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Speichern' })).toBeInTheDocument()
  })

  it('answers a refused request made inside the app, which is then resent with the purpose', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response('{"detail":"refused"}', { status: 428 }))
      .mockResolvedValueOnce(new Response('{"ok":1}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    renderShell(<StatsProbe />)

    fireEvent.click(await screen.findByRole('button', { name: /probe/ }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('Case reference'), { target: { value: 'case-0001' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('button', { name: 'probe 1' })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const resent = new Headers(fetchMock.mock.calls[1]![1]!.headers as HeadersInit)
    expect(resent.get(PURPOSE_HEADER)).toBe('case_ref=case-0001')
    expect(screen.getByRole('button', { name: 'Purpose: case-0001' })).toBeInTheDocument()
  })
})
