import { describe, expect, it } from 'vitest'
import { ApiError } from './client'
import { retryPolicy } from './queryClient'

describe('retryPolicy', () => {
  it('returns false for a 4xx ApiError (never retry deterministic client errors)', () => {
    const err = new ApiError(422, 'Unprocessable Entity')
    expect(retryPolicy(0, err)).toBe(false)
  })

  it('returns true on first failure for a 5xx error (retry once)', () => {
    const err = new ApiError(500, 'Internal Server Error')
    expect(retryPolicy(0, err)).toBe(true)
  })

  it('returns false after one retry for a 5xx error (only one retry allowed)', () => {
    const err = new ApiError(500, 'Internal Server Error')
    expect(retryPolicy(1, err)).toBe(false)
  })

  it('returns true on first failure for a generic (non-ApiError) error', () => {
    expect(retryPolicy(0, new Error('network failure'))).toBe(true)
  })

  it('returns false after one retry for a generic error', () => {
    expect(retryPolicy(1, new Error('network failure'))).toBe(false)
  })
})
