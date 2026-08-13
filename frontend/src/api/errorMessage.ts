import { ApiError } from './client'

export type ErrorKey =
  | 'common.error_request'
  | 'common.error_unknown'
  | 'common.error_network'
  | 'common.error_forbidden'

export type ErrorDescriptor = {
  key: ErrorKey
  vars?: Record<string, string | number>
}

/** The only sanctioned path from a thrown error to user-visible text.
 *  Never render err.message, err.detail, or response bodies. */
export function describeError(err: unknown): ErrorDescriptor {
  if (err instanceof ApiError) {
    // Dev-only visibility; the body is generic post-backend-fix anyway.
    console.debug('API error detail', err.status, err.detail)
    if (err.status === 403) {
      // Since ADR 0017 a 403 is nearly always the project claim — either
      // the selection fell outside it or the claim itself went away.
      return { key: 'common.error_forbidden', vars: { status: err.status } }
    }
    return { key: 'common.error_request', vars: { status: err.status } }
  }
  if (err instanceof TypeError) {
    // fetch() network-level failure (server unreachable, DNS, CORS)
    return { key: 'common.error_network' }
  }
  return { key: 'common.error_unknown' }
}
