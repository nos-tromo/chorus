/** API base: explicit VITE_API_BASE_URL wins; otherwise derive from
 *  Vite's base path so the SPA works under /chorus/ and at root alike. */
export function apiBase(
  override: string | undefined = import.meta.env.VITE_API_BASE_URL,
  base: string = import.meta.env.BASE_URL,
): string {
  const raw = override ?? (base === '/' ? '' : base)
  return raw.replace(/\/+$/, '')
}

const BASE = apiBase()

export class ApiError extends Error {
  constructor(readonly status: number, readonly detail: unknown) {
    super(`API ${status}`)
    this.name = 'ApiError'
  }
}

export function url(path: string): string {
  return `${BASE}${path}`
}

async function handle<T>(res: Response): Promise<T> {
  const text = await res.text()
  let body: unknown = text
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    /* keep raw text */
  }
  if (!res.ok) {
    const detail =
      body && typeof body === 'object' && 'detail' in body
        ? (body as { detail: unknown }).detail
        : body
    throw new ApiError(res.status, detail)
  }
  return body as T
}

export async function apiGet<T>(
  path: string,
  params?: Record<string, string | number | undefined | null>,
  signal?: AbortSignal,
): Promise<T> {
  const qs = params
    ? '?' +
      new URLSearchParams(
        Object.entries(params)
          .filter(([, v]) => v !== undefined && v !== null && v !== '')
          .map(([k, v]) => [k, String(v)]),
      ).toString()
    : ''
  return handle<T>(await fetch(url(path + qs), { signal }))
}

export async function apiPost<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = {}
  let payload: BodyInit | undefined
  if (body instanceof FormData) {
    payload = body
  } else if (body !== undefined) {
    headers['content-type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  return handle<T>(await fetch(url(path), { method: 'POST', headers, body: payload, signal }))
}
