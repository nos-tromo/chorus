import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../api/client'

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => apiGet<{ status: string }>('/health', undefined, signal),
  })
}
