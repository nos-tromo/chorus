import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import type { AppConfig } from '../api/types'

// ── mock api/client (must come before component import) ───────────────────────

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

// Config mock — start with ingestion enabled; individual tests can override.
const mockConfig: AppConfig = { language: 'en', ingestion_enabled: true, version: '0.1.0' }

vi.mock('../api/config', () => ({
  fetchConfig: vi.fn((): Promise<AppConfig> => Promise.resolve(mockConfig)),
}))

// Import mocked modules AFTER vi.mock declarations (hoisted)
import { apiGet, apiPost, ApiError } from '../api/client'
import { fetchConfig } from '../api/config'
import { ConfigProvider } from '../config/ConfigContext'
import { Ingestion } from './Ingestion'

// ── helpers ───────────────────────────────────────────────────────────────────

function renderIngestion(ingestionEnabled = true) {
  vi.mocked(fetchConfig).mockResolvedValue({
    language: 'en',
    ingestion_enabled: ingestionEnabled,
    version: '0.1.0',
  })

  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })

  function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <ConfigProvider>{children}</ConfigProvider>
      </QueryClientProvider>
    )
  }

  return render(<Ingestion />, { wrapper: Wrapper })
}

const MIGRATIONS_UPTODATE = { applied: ['001', '002'], pending: [] as string[] }
const MIGRATIONS_PENDING = { applied: ['001'], pending: ['002', '003'] }

function mockGetIngestion({
  migrations = MIGRATIONS_UPTODATE,
  job = null as Record<string, unknown> | null,
}: {
  migrations?: { applied: string[]; pending: string[] }
  job?: Record<string, unknown> | null
} = {}) {
  vi.mocked(apiGet).mockImplementation((path: string) => {
    if (path === '/ingestion/migrations') return Promise.resolve(migrations)
    if (path.startsWith('/ingestion/jobs/'))
      return Promise.resolve(
        job ?? { id: 'j1', kind: 'ingest', status: 'done', result: null, error: null },
      )
    return Promise.reject(new Error(`unexpected GET ${path}`))
  })
}

// ── tests ─────────────────────────────────────────────────────────────────────

describe('Ingestion', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // ── (a) migrations list renders ──────────────────────────────────────────

  it('(a1) renders "Schema is up to date" when no pending migrations', async () => {
    mockGetIngestion({ migrations: MIGRATIONS_UPTODATE })
    renderIngestion()

    expect(await screen.findByText(/schema is up to date/i)).toBeTruthy()
  })

  it('(a2) renders applied + pending migration versions', async () => {
    mockGetIngestion({ migrations: MIGRATIONS_PENDING })
    renderIngestion()

    // Pending versions shown in the warning
    expect(await screen.findByText(/pending migrations/i)).toBeTruthy()

    // Applied versions list
    expect(screen.getByText(/001/)).toBeTruthy()
  })

  it('(a3) apply-migrations button is shown when there are pending migrations', async () => {
    mockGetIngestion({ migrations: MIGRATIONS_PENDING })
    renderIngestion()

    expect(await screen.findByRole('button', { name: /apply migrations/i })).toBeTruthy()
  })

  it('(a4) apply-migrations 409 shows a "busy" Banner', async () => {
    mockGetIngestion({ migrations: MIGRATIONS_PENDING })
    renderIngestion()

    await screen.findByRole('button', { name: /apply migrations/i })

    // Mock the POST to return a 409 ApiError
    vi.mocked(apiPost).mockRejectedValueOnce(new ApiError(409, 'server busy'))

    fireEvent.click(screen.getByRole('button', { name: /apply migrations/i }))

    // Expect a busy Banner to appear
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy()
    })
  })

  // ── (b) file upload + ingest ─────────────────────────────────────────────

  it('(b1) start-ingestion button is disabled when no files selected', async () => {
    mockGetIngestion()
    renderIngestion()

    await screen.findByText(/schema is up to date/i)
    const btn = screen.getByRole('button', { name: /start ingestion/i })
    expect((btn as HTMLButtonElement).disabled).toBe(true)
  })

  it('(b2) selecting files enables the start-ingestion button', async () => {
    mockGetIngestion()
    renderIngestion()

    await screen.findByText(/schema is up to date/i)

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    expect(fileInput).toBeTruthy()

    const file = new File(['col1,col2\nv1,v2'], 'postings.csv', { type: 'text/csv' })
    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => {
      const btn = screen.getByRole('button', { name: /start ingestion/i })
      expect((btn as HTMLButtonElement).disabled).toBe(false)
    })
  })

  it('(b3) submit calls startIngest with files and then_resolve flag', async () => {
    vi.mocked(apiGet).mockImplementation((path: string) => {
      if (path === '/ingestion/migrations') return Promise.resolve(MIGRATIONS_UPTODATE)
      if (path === '/ingestion/jobs/j1')
        return Promise.resolve({
          id: 'j1',
          kind: 'ingest',
          status: 'done',
          result: { counts: { postings: 5 } },
          error: null,
        })
      return Promise.reject(new Error(`unexpected GET ${path}`))
    })
    vi.mocked(apiPost).mockResolvedValueOnce({
      job_id: 'j1',
      status: 'queued',
      kind: 'ingest',
    })

    renderIngestion()
    await screen.findByText(/schema is up to date/i)

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['col1,col2\nv1,v2'], 'postings.csv', { type: 'text/csv' })
    fireEvent.change(fileInput, { target: { files: [file] } })

    // Tick the "run resolution after" checkbox
    const checkbox = screen.getByRole('checkbox', { name: /run resolution after/i })
    fireEvent.click(checkbox)

    await waitFor(() => {
      const btn = screen.getByRole('button', { name: /start ingestion/i })
      expect((btn as HTMLButtonElement).disabled).toBe(false)
    })

    fireEvent.click(screen.getByRole('button', { name: /start ingestion/i }))

    await waitFor(() => expect(apiPost).toHaveBeenCalled())
    const [[path, body]] = vi.mocked(apiPost).mock.calls
    expect(path).toBe('/ingestion/ingest')
    expect(body).toBeInstanceOf(FormData)
    const fd = body as FormData
    expect(fd.get('then_resolve')).toBe('true')
    expect(fd.getAll('files').length).toBe(1)
  })

  // ── (c) job running: controls disabled + indicator shown ─────────────────

  it('(c1) while a job is running, controls are disabled and progress is shown', async () => {
    // Start ingest returns queued
    vi.mocked(apiPost).mockResolvedValueOnce({ job_id: 'j1', status: 'queued', kind: 'ingest' })
    // Poll keeps returning running
    vi.mocked(apiGet).mockImplementation((path: string) => {
      if (path === '/ingestion/migrations') return Promise.resolve(MIGRATIONS_UPTODATE)
      if (path === '/ingestion/jobs/j1')
        return Promise.resolve({
          id: 'j1',
          kind: 'ingest',
          status: 'running',
          result: null,
          error: null,
        })
      return Promise.reject(new Error(`unexpected GET ${path}`))
    })

    renderIngestion()
    await screen.findByText(/schema is up to date/i)

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['col1'], 'postings.csv', { type: 'text/csv' })
    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => {
      const btn = screen.getByRole('button', { name: /start ingestion/i })
      expect((btn as HTMLButtonElement).disabled).toBe(false)
    })

    fireEvent.click(screen.getByRole('button', { name: /start ingestion/i }))

    // Running indicator appears
    expect(await screen.findByText(/ingestion running/i)).toBeTruthy()

    // Resolve button should be disabled while busy
    const resolveBtn = screen.getByRole('button', { name: /run resolution/i })
    expect((resolveBtn as HTMLButtonElement).disabled).toBe(true)
  })

  // ── (d) job done: counts table renders ───────────────────────────────────

  it('(d) when polled job is done, counts table is rendered', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({ job_id: 'j2', status: 'queued', kind: 'ingest' })
    vi.mocked(apiGet).mockImplementation((path: string) => {
      if (path === '/ingestion/migrations') return Promise.resolve(MIGRATIONS_UPTODATE)
      if (path === '/ingestion/jobs/j2')
        return Promise.resolve({
          id: 'j2',
          kind: 'ingest',
          status: 'done',
          result: {
            counts: { postings: 10, comments: 5 },
            dropped: {},
            filtered: {},
            skipped: [],
          },
          error: null,
        })
      return Promise.reject(new Error(`unexpected GET ${path}`))
    })

    renderIngestion()
    await screen.findByText(/schema is up to date/i)

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['col1'], 'postings.csv', { type: 'text/csv' })
    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => {
      expect(
        (screen.getByRole('button', { name: /start ingestion/i }) as HTMLButtonElement).disabled,
      ).toBe(false)
    })

    fireEvent.click(screen.getByRole('button', { name: /start ingestion/i }))

    // Counts table shows stages
    expect(await screen.findByText('postings')).toBeTruthy()
    expect(screen.getByText('comments')).toBeTruthy()
    // Count values
    expect(screen.getByText('10')).toBeTruthy()
    expect(screen.getByText('5')).toBeTruthy()
  })

  // ── (e) job error: error Banner shown ────────────────────────────────────

  it('(e) when polled job is error, error Banner is shown', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({ job_id: 'j3', status: 'queued', kind: 'ingest' })
    vi.mocked(apiGet).mockImplementation((path: string) => {
      if (path === '/ingestion/migrations') return Promise.resolve(MIGRATIONS_UPTODATE)
      if (path === '/ingestion/jobs/j3')
        return Promise.resolve({
          id: 'j3',
          kind: 'ingest',
          status: 'error',
          result: null,
          error: 'CSV parse failed',
        })
      return Promise.reject(new Error(`unexpected GET ${path}`))
    })

    renderIngestion()
    await screen.findByText(/schema is up to date/i)

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['col1'], 'postings.csv', { type: 'text/csv' })
    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => {
      expect(
        (screen.getByRole('button', { name: /start ingestion/i }) as HTMLButtonElement).disabled,
      ).toBe(false)
    })

    fireEvent.click(screen.getByRole('button', { name: /start ingestion/i }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/CSV parse failed/i)
  })

  // ── (f) ingestion disabled notice ────────────────────────────────────────

  it('(f) shows disabled notice when ingestion_enabled=false', async () => {
    mockGetIngestion()
    renderIngestion(false)

    expect(await screen.findByText(/INGESTION_UI_ENABLED/i)).toBeTruthy()
  })
})
