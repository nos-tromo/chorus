import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
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

// Same treatment for the project seam. What setActive *does* — header,
// storage, cache eviction, remount — is ProjectContext.test.tsx's subject;
// here we only care that the sidebar offers the choice and reports it.
const setActive = vi.fn()
const mockProject = { projects: ['default'], active: 'default' }

vi.mock('../project/ProjectContext', () => ({
  useProject: vi.fn(() => ({ ...mockProject, setActive })),
}))

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

function setProjects(projects: string[], active = projects[0]) {
  mockProject.projects = projects
  mockProject.active = active
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

// Reset to disabled / single-project before each test.
beforeEach(() => {
  setIngestion(false)
  setProjects(['default'])
  setActive.mockClear()
})

describe('Sidebar', () => {
  it('renders the tool nav links', () => {
    renderSidebar()
    // Top-level agent link
    expect(screen.getByRole("link", { name: /chat/i })).toBeInTheDocument()
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
      screen.getByRole('link', { name: /graph explorer/i }),
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

describe('Sidebar project switcher', () => {
  it('stays out of the way when there is only one project', () => {
    setProjects(['default'])
    renderSidebar()
    expect(screen.queryByLabelText('Project')).not.toBeInTheDocument()
  })

  it('lists every allowed project with the active one selected', () => {
    setProjects(['alpha', 'beta'], 'alpha')
    renderSidebar()
    const select = screen.getByLabelText('Project') as HTMLSelectElement
    expect(select.value).toBe('alpha')
    expect([...select.options].map((o) => o.value)).toEqual(['alpha', 'beta'])
  })

  it('asks the provider to switch when another project is chosen', () => {
    setProjects(['alpha', 'beta'], 'alpha')
    renderSidebar()
    fireEvent.change(screen.getByLabelText('Project'), { target: { value: 'beta' } })
    expect(setActive).toHaveBeenCalledWith('beta')
  })
})
