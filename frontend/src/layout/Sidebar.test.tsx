import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import type { AppConfig } from '../api/types'
import { catalogs, format } from '../i18n'
import type { Strings } from '../i18n'

// ---- shared mock state ------------------------------------------------
const mockConfig: AppConfig = {
  language: 'en',
  ingestion_enabled: false,
  version: '0.0.0',
}

// Mock the entire ConfigContext module so no Provider / real fetch is needed.
vi.mock('../config/ConfigContext', () => ({
  useConfig: vi.fn((): AppConfig => ({ ...mockConfig })),
  useT: vi.fn(
    () =>
      (key: keyof Strings, vars?: Record<string, string | number>) =>
        format(catalogs[mockConfig.language][key], vars),
  ),
}))

// Sidebar mounts VersionBadge, which fetches its own version — mock it out too
// so no Provider / real fetch is needed for that either.
vi.mock('../api/config', () => ({ getVersion: vi.fn().mockResolvedValue({ version: '' }) }))

import { useConfig, useT } from '../config/ConfigContext'

// Helpers to re-stub both hooks together (keeps mockConfig in sync).
function setIngestion(enabled: boolean) {
  mockConfig.ingestion_enabled = enabled
  vi.mocked(useConfig).mockReturnValue({ ...mockConfig })
  vi.mocked(useT).mockReturnValue(
    (key: keyof Strings, vars?: Record<string, string | number>) =>
      format(catalogs[mockConfig.language][key], vars),
  )
}

function renderSidebar() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

// Reset to disabled before each test.
beforeEach(() => setIngestion(false))

describe('Sidebar', () => {
  it('renders the tool nav links', () => {
    renderSidebar()
    // Top-level agent link
    expect(screen.getByRole('link', { name: /agent/i })).toBeInTheDocument()
    // Entities group
    expect(
      screen.getByRole('link', { name: /posts mentioning/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: /authors mentioning/i }),
    ).toBeInTheDocument()
    // Topics group
    expect(
      screen.getByRole('link', { name: /topic co-occurrence/i }),
    ).toBeInTheDocument()
    // Networks group
    expect(
      screen.getByRole('link', { name: /social network/i }),
    ).toBeInTheDocument()
  })

  it('hides the ingestion link when ingestion_enabled=false', () => {
    setIngestion(false)
    renderSidebar()
    expect(
      screen.queryByRole('link', { name: /ingestion/i }),
    ).not.toBeInTheDocument()
  })

  it('shows the ingestion link when ingestion_enabled=true', () => {
    setIngestion(true)
    renderSidebar()
    expect(
      screen.getByRole('link', { name: /ingestion/i }),
    ).toBeInTheDocument()
  })
})
