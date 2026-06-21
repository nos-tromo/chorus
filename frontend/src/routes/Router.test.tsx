import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { ConfigProvider } from '../config/ConfigContext'
import { Shell } from '../layout/Shell'
import { AppRoutes } from './Router'
import type { AppConfig } from '../api/types'

vi.mock('../api/config', () => ({
  fetchConfig: vi.fn(
    (): Promise<AppConfig> =>
      Promise.resolve({ language: 'en', ingestion_enabled: false, version: '0.1.0' }),
  ),
}))

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

function Wrapper({ initialEntries }: { initialEntries: string[] }) {
  return (
    <QueryClientProvider client={makeClient()}>
      <ConfigProvider>
        <MemoryRouter initialEntries={initialEntries}>
          <Shell>
            <AppRoutes />
          </Shell>
        </MemoryRouter>
      </ConfigProvider>
    </QueryClientProvider>
  )
}

describe('AppRoutes', () => {
  it('renders Agent stub at /agent', async () => {
    render(<Wrapper initialEntries={['/agent']} />)
    expect(await screen.findByRole('heading', { name: /agent/i })).toBeTruthy()
  })

  it('redirects unknown path to / and renders the Landing page', async () => {
    render(<Wrapper initialEntries={['/nope']} />)
    expect(await screen.findByRole('heading', { name: /chorus/i, level: 1 })).toBeTruthy()
  })
})
