import { useMutation } from '@tanstack/react-query'
import { callTool } from '../api/tools'

export function useToolCall<TOut>(name: string) {
  return useMutation({
    mutationFn: (payload: unknown) => callTool<TOut>(name, payload),
  })
}
