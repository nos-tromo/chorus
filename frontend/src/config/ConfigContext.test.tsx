import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { ConfigProvider, useConfig, useT } from './ConfigContext'
import type { AppConfig } from '../api/types'

// Mock fetchConfig so no real fetch happens in unit tests.
vi.mock('../api/config', () => ({
  fetchConfig: vi.fn(
    (): Promise<AppConfig> =>
      Promise.resolve({ language: 'de', ingestion_enabled: true, version: '0.1.0' }),
  ),
}))

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
}

function ProbeComponent() {
  const config = useConfig()
  const t = useT()
  return (
    <div>
      <span data-testid="ingestion">{String(config.ingestion_enabled)}</span>
      <span data-testid="caption">{t('landing.caption')}</span>
    </div>
  )
}

describe('ConfigProvider + useConfig + useT', () => {
  it('renders children with German catalog after config loads', async () => {
    render(
      <QueryClientProvider client={makeClient()}>
        <ConfigProvider>
          <ProbeComponent />
        </ConfigProvider>
      </QueryClientProvider>,
    )

    // Wait for the post-load render (German caption from de catalog)
    const caption = await screen.findByTestId('caption')
    // German value from de.ts: 'GraphRAG für die Analyse sozialer Netzwerke'
    expect(caption.textContent).toBe('GraphRAG für die Analyse sozialer Netzwerke')

    // useConfig().ingestion_enabled === true
    expect(screen.getByTestId('ingestion').textContent).toBe('true')
  })
})
