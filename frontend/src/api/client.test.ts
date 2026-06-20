import { describe, expect, it, vi, afterEach } from 'vitest'
import { apiGet, apiPost } from './client'

afterEach(() => vi.restoreAllMocks())

describe('api client', () => {
  it('GET parses JSON and sends no identity header', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      new Response(JSON.stringify({ ok: 1 }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const out = await apiGet<{ ok: number }>('/health')
    expect(out).toEqual({ ok: 1 })
    const headers = (fetchMock.mock.calls[0]?.[1]?.headers ?? {}) as Record<string, string>
    expect(Object.keys(headers).map((k) => k.toLowerCase())).not.toContain('x-auth-user')
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
