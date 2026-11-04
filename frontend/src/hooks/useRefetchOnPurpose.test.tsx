import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { clearAllPurposes, setPurpose } from '@infra/ui'
import { ApiError } from '../api/client'
import { PURPOSE_SCOPE } from '../api/purpose'
import { retryPolicy } from '../api/queryClient'
import { useRefetchOnPurpose } from './useRefetchOnPurpose'

afterEach(() => clearAllPurposes())

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: retryPolicy } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

function useProbe(key: string, fail: () => ApiError | null) {
  useRefetchOnPurpose()
  return useQuery({
    queryKey: [key],
    queryFn: () => {
      const err = fail()
      if (err) throw err
      return 'ok'
    },
  })
}

describe('useRefetchOnPurpose', () => {
  it('reloads a query refused with 428 once a purpose is stated', async () => {
    let refuse = true
    const queryFn = vi.fn(() => (refuse ? new ApiError(428, 'refused') : null))
    const { result } = renderHook(() => useProbe('stats', queryFn), { wrapper })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(queryFn).toHaveBeenCalledTimes(1)

    refuse = false
    act(() => {
      setPurpose(PURPOSE_SCOPE, { caseRef: 'case-0001' })
    })
    await waitFor(() => expect(result.current.data).toBe('ok'))
    expect(queryFn).toHaveBeenCalledTimes(2)
  })

  it('leaves other failures alone', async () => {
    const queryFn = vi.fn(() => new ApiError(403, 'forbidden'))
    const { result } = renderHook(() => useProbe('gated', queryFn), { wrapper })
    await waitFor(() => expect(result.current.isError).toBe(true))

    act(() => {
      setPurpose(PURPOSE_SCOPE, { caseRef: 'case-0001' })
    })
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(queryFn).toHaveBeenCalledTimes(1)
  })
})
