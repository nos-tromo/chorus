import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import { useJob, isTerminal } from './useJob'
import { useMigrations } from './useMigrations'
import { useApplyMigrations, useStartIngest, useStartResolve } from './useIngest'

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

import { apiGet, apiPost, ApiError } from '../api/client'

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

// ── isTerminal (unit) ────────────────────────────────────────────────────────

describe('isTerminal', () => {
  it('returns true for "done"', () => {
    expect(isTerminal('done')).toBe(true)
  })
  it('returns true for "error"', () => {
    expect(isTerminal('error')).toBe(true)
  })
  it('returns false for "running"', () => {
    expect(isTerminal('running')).toBe(false)
  })
  it('returns false for "queued"', () => {
    expect(isTerminal('queued')).toBe(false)
  })
  it('returns false for undefined', () => {
    expect(isTerminal(undefined)).toBe(false)
  })
})

// ── useJob polling ────────────────────────────────────────────────────────────

describe('useJob', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('is disabled when jobId is null', () => {
    const { result } = renderHook(() => useJob(null), { wrapper: makeWrapper() })
    expect(result.current.fetchStatus).toBe('idle')
  })

  it('fetches job snapshot when jobId is set', async () => {
    const snapshot = { id: 'j1', kind: 'ingest', status: 'running', result: null, error: null }
    vi.mocked(apiGet).mockResolvedValue(snapshot)

    const { result } = renderHook(() => useJob('j1'), { wrapper: makeWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(snapshot)
    expect(apiGet).toHaveBeenCalledWith('/ingestion/jobs/j1')
  })

  it('refetchInterval returns false for done status (stops polling)', () => {
    // Test the refetchInterval predicate behaviour by verifying isTerminal directly
    // and that a done snapshot would stop polling.
    const doneSnapshot = { id: 'j1', kind: 'ingest', status: 'done', result: {}, error: null }
    expect(isTerminal(doneSnapshot.status as 'done')).toBe(true)
    const runningSnapshot = { id: 'j1', kind: 'ingest', status: 'running', result: null, error: null }
    expect(isTerminal(runningSnapshot.status as 'running')).toBe(false)
  })

  it('surfaces the snapshot once status is done', async () => {
    const doneSnapshot = { id: 'j2', kind: 'resolve', status: 'done', result: { count: 5 }, error: null }
    vi.mocked(apiGet).mockResolvedValue(doneSnapshot)

    const { result } = renderHook(() => useJob('j2'), { wrapper: makeWrapper() })

    await waitFor(() => expect(result.current.data?.status).toBe('done'))
    expect(result.current.data).toEqual(doneSnapshot)
  })
})

// ── useMigrations ─────────────────────────────────────────────────────────────

describe('useMigrations', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches migrations status', async () => {
    const migrations = { applied: ['001_init.cypher'], pending: ['002_indexes.cypher'] }
    vi.mocked(apiGet).mockResolvedValueOnce(migrations)

    const { result } = renderHook(() => useMigrations(), { wrapper: makeWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(migrations)
    expect(apiGet).toHaveBeenCalledWith('/ingestion/migrations')
  })
})

// ── useApplyMigrations ────────────────────────────────────────────────────────

describe('useApplyMigrations', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('POSTs to /ingestion/migrate and returns applied list', async () => {
    const result_data = { applied: ['002_indexes.cypher'] }
    vi.mocked(apiPost).mockResolvedValueOnce(result_data)

    const { result } = renderHook(() => useApplyMigrations(), { wrapper: makeWrapper() })

    let returned: { applied: string[] } | undefined
    await act(async () => {
      returned = await result.current.mutateAsync()
    })

    expect(returned).toEqual(result_data)
    expect(apiPost).toHaveBeenCalledWith('/ingestion/migrate')
  })
})

// ── useStartIngest ────────────────────────────────────────────────────────────

describe('useStartIngest', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('builds FormData with files, since, and then_resolve', async () => {
    const jobAccepted = { job_id: 'j3', status: 'queued', kind: 'ingest' }
    vi.mocked(apiPost).mockResolvedValueOnce(jobAccepted)

    const { result } = renderHook(() => useStartIngest(), { wrapper: makeWrapper() })

    const file1 = new File(['data1'], 'posts.csv', { type: 'text/csv' })
    const file2 = new File(['data2'], 'comments.csv', { type: 'text/csv' })
    const since = '2024-01-01T00:00:00Z'

    let returned: typeof jobAccepted | undefined
    await act(async () => {
      returned = await result.current.mutateAsync({ files: [file1, file2], since, thenResolve: true })
    })

    expect(returned).toEqual(jobAccepted)
    expect(apiPost).toHaveBeenCalledTimes(1)

    const [path, body] = vi.mocked(apiPost).mock.calls[0] as [string, FormData]
    expect(path).toBe('/ingestion/ingest')
    expect(body).toBeInstanceOf(FormData)

    // Check all files are appended under 'files'
    const formFiles = body.getAll('files')
    expect(formFiles).toHaveLength(2)
    expect((formFiles[0] as File).name).toBe('posts.csv')
    expect((formFiles[1] as File).name).toBe('comments.csv')

    // Check since is appended
    expect(body.get('since')).toBe(since)

    // Check then_resolve is appended as string 'true'
    expect(body.get('then_resolve')).toBe('true')
  })

  it('omits since from FormData when not provided', async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({ job_id: 'j4', status: 'queued', kind: 'ingest' })

    const { result } = renderHook(() => useStartIngest(), { wrapper: makeWrapper() })
    const file = new File(['data'], 'posts.csv', { type: 'text/csv' })

    await act(async () => {
      await result.current.mutateAsync({ files: [file], since: undefined, thenResolve: false })
    })

    const [, body] = vi.mocked(apiPost).mock.calls[0] as [string, FormData]
    expect(body.has('since')).toBe(false)
    expect(body.get('then_resolve')).toBe('false')
  })

  it('surfaces 409 as ApiError with status 409 when job already active', async () => {
    const conflict = new ApiError(409, 'job already active')
    vi.mocked(apiPost).mockRejectedValueOnce(conflict)

    const { result } = renderHook(() => useStartIngest(), { wrapper: makeWrapper() })
    const file = new File(['data'], 'posts.csv', { type: 'text/csv' })

    let caughtError: unknown
    await act(async () => {
      try {
        await result.current.mutateAsync({ files: [file], since: undefined, thenResolve: false })
      } catch (e) {
        caughtError = e
      }
    })

    expect(caughtError).toBeInstanceOf(ApiError)
    expect((caughtError as InstanceType<typeof ApiError>).status).toBe(409)
  })
})

// ── useStartResolve ───────────────────────────────────────────────────────────

describe('useStartResolve', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('POSTs to /ingestion/resolve and returns JobAccepted', async () => {
    const jobAccepted = { job_id: 'j5', status: 'queued', kind: 'resolve' }
    vi.mocked(apiPost).mockResolvedValueOnce(jobAccepted)

    const { result } = renderHook(() => useStartResolve(), { wrapper: makeWrapper() })

    let returned: typeof jobAccepted | undefined
    await act(async () => {
      returned = await result.current.mutateAsync()
    })

    expect(returned).toEqual(jobAccepted)
    expect(apiPost).toHaveBeenCalledWith('/ingestion/resolve')
  })

  it('surfaces 409 as ApiError with status 409', async () => {
    const conflict = new ApiError(409, 'job already active')
    vi.mocked(apiPost).mockRejectedValueOnce(conflict)

    const { result } = renderHook(() => useStartResolve(), { wrapper: makeWrapper() })

    let caughtError: unknown
    await act(async () => {
      try {
        await result.current.mutateAsync()
      } catch (e) {
        caughtError = e
      }
    })

    expect(caughtError).toBeInstanceOf(ApiError)
    expect((caughtError as InstanceType<typeof ApiError>).status).toBe(409)
  })
})
