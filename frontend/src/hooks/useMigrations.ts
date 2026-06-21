import { useQuery } from '@tanstack/react-query'
import { getMigrations } from '../api/ingestion'
import type { MigrationsStatus } from '../api/types'

export function useMigrations() {
  return useQuery<MigrationsStatus>({
    queryKey: ['migrations'],
    queryFn: getMigrations,
  })
}
