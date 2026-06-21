import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import { useHealth } from './useHealth'
import { useTools } from './useTools'
import { useToolCall } from './useToolCall'
import type { ToolsList } from '../api/types'

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

import { apiGet, apiPost } from '../api/client'

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

describe('useHealth', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('surfaces mocked {status:"ok"} from GET /health', async () => {
    vi.mocked(apiGet).mockResolvedValueOnce({ status: 'ok' })

    const { result } = renderHook(() => useHealth(), { wrapper: makeWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data).toEqual({ status: 'ok' })
    expect(apiGet).toHaveBeenCalledWith('/health', undefined, expect.any(AbortSignal))
  })
})

describe('useTools', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  const mockTools: ToolsList = [
    {
      name: 'posts_mentioning',
      description: 'Get posts mentioning an entity',
      input_schema: {},
      output_schema: {},
    },
    {
      name: 'authors_mentioning',
      description: 'Get authors mentioning an entity',
      input_schema: {},
      output_schema: {},
    },
  ]

  it('surfaces mocked tools list from GET /tools', async () => {
    vi.mocked(apiGet).mockResolvedValueOnce(mockTools)

    const { result } = renderHook(() => useTools(), { wrapper: makeWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data).toEqual(mockTools)
    expect(apiGet).toHaveBeenCalledWith('/tools')
  })
})

describe('useToolCall', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('POSTs to /tools/{name} with payload and returns mocked result', async () => {
    const mockResult = { posts: [{ uuid: 'abc', text: 'hello' }] }
    vi.mocked(apiPost).mockResolvedValueOnce(mockResult)

    const { result } = renderHook(() => useToolCall<typeof mockResult>('posts_mentioning'), {
      wrapper: makeWrapper(),
    })

    const payload = { entity: 'Alice', time_range: { start: '2024-01-01', end: '2024-12-31' } }
    let returned: typeof mockResult | undefined
    await waitFor(async () => {
      returned = await result.current.mutateAsync(payload)
    })

    expect(returned).toEqual(mockResult)
    expect(apiPost).toHaveBeenCalledWith('/tools/posts_mentioning', payload)
  })
})
