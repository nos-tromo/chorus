import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getJob } from '../api/ingestion'
import type { JobSnapshot, JobStatus } from '../api/types'

export function isTerminal(s?: JobStatus): boolean {
  return s === 'done' || s === 'error'
}

export function useJob(jobId: string | null) {
  const qc = useQueryClient()
  return useQuery<JobSnapshot>({
    queryKey: ['job', jobId],
    queryFn: () => getJob(jobId!),
    enabled: jobId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (isTerminal(status)) {
        void qc.invalidateQueries({ queryKey: ['migrations'] })
        return false
      }
      return 1500
    },
  })
}
