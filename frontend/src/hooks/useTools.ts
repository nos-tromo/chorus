import { useQuery } from '@tanstack/react-query'
import { listTools } from '../api/tools'

export function useTools() {
  return useQuery({
    queryKey: ['tools'],
    queryFn: listTools,
  })
}
