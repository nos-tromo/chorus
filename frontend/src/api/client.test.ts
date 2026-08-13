import { describe, expect, it, vi, afterEach } from 'vitest'
import { apiBase, apiGet, apiPost, setActiveProjectHeader } from './client'

afterEach(() => {
  vi.restoreAllMocks()
  setActiveProjectHeader(null)
})

/** Headers of the nth fetch call, as a case-insensitive Headers object. */
function sentHeaders(fetchMock: ReturnType<typeof vi.fn<typeof fetch>>, call = 0): Headers {
  const init = fetchMock.mock.calls[call]?.[1] ?? {}
  return new Headers(init.headers as HeadersInit | undefined)
}

describe('api client', () => {
  it('GET parses JSON and sends no identity header', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      new Response(JSON.stringify({ ok: 1 }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const out = await apiGet<{ ok: number }>('/health')
    expect(out).toEqual({ ok: 1 })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const init = fetchMock.mock.calls[0][1] ?? {}
    const headers = new Headers(init.headers as HeadersInit | undefined)
    expect(headers.has('x-auth-user')).toBe(false)
  })

  it('throws ApiError with detail on non-2xx', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'nope' }), { status: 422 })))
    await expect(apiGet('/tools/x')).rejects.toMatchObject({ status: 422, detail: 'nope' })
  })

  it('POST sends FormData without forcing content-type', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response('{}', { status: 202 }))
    vi.stubGlobal('fetch', fetchMock)
    await apiPost('/ingestion/ingest', new FormData())
    const init = fetchMock.mock.calls[0]![1]!
    expect(init.body).toBeInstanceOf(FormData)
    expect((init.headers as Record<string, string>)['content-type']).toBeUndefined()
  })
})

describe('active project header', () => {
  it('is absent until a project is selected', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await apiGet('/stats')
    await apiPost('/agent/query', { question: 'hi' })
    expect(sentHeaders(fetchMock, 0).has('x-chorus-project')).toBe(false)
    expect(sentHeaders(fetchMock, 1).has('x-chorus-project')).toBe(false)
  })

  it('rides along on GET once selected', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    setActiveProjectHeader('alpha')
    await apiGet('/stats', { limit: 5 })
    expect(sentHeaders(fetchMock).get('x-chorus-project')).toBe('alpha')
  })

  it('rides along on JSON POST alongside content-type', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    setActiveProjectHeader('beta')
    await apiPost('/agent/query', { question: 'hi' })
    const headers = sentHeaders(fetchMock)
    expect(headers.get('x-chorus-project')).toBe('beta')
    expect(headers.get('content-type')).toBe('application/json')
  })

  it('rides along on FormData POST without forcing content-type', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response('{}', { status: 202 }))
    vi.stubGlobal('fetch', fetchMock)
    setActiveProjectHeader('beta')
    await apiPost('/ingestion/ingest', new FormData())
    const init = fetchMock.mock.calls[0]![1]!
    expect((init.headers as Record<string, string>)['content-type']).toBeUndefined()
    expect(sentHeaders(fetchMock).get('x-chorus-project')).toBe('beta')
  })

  it('clears back to absent when deselected', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    setActiveProjectHeader('alpha')
    setActiveProjectHeader(null)
    await apiGet('/stats')
    expect(sentHeaders(fetchMock).has('x-chorus-project')).toBe(false)
  })
})

describe('apiBase', () => {
  it('uses an explicit VITE_API_BASE_URL override verbatim (trailing slash trimmed)', () => {
    expect(apiBase('http://elsewhere/', '/chorus/')).toBe('http://elsewhere')
  })
  it('derives from BASE_URL when no override is set', () => {
    expect(apiBase(undefined, '/chorus/')).toBe('/chorus')
  })
  it('is empty (same-origin root) at root BASE_URL with no override', () => {
    expect(apiBase(undefined, '/')).toBe('')
  })
})
