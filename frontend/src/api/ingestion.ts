import { apiGet, apiPost } from './client'
import type { JobAccepted, JobSnapshot, MigrationsStatus } from './types'

export const getMigrations = () => apiGet<MigrationsStatus>('/ingestion/migrations')

export const applyMigrations = () => apiPost<{ applied: string[] }>('/ingestion/migrate')

export const startIngest = (
  files: File[],
  since: string | undefined,
  thenResolve: boolean,
): Promise<JobAccepted> => {
  const form = new FormData()
  for (const file of files) {
    form.append('files', file)
  }
  if (since !== undefined) {
    form.append('since', since)
  }
  form.append('then_resolve', String(thenResolve))
  return apiPost<JobAccepted>('/ingestion/ingest', form)
}

export const startResolve = () => apiPost<JobAccepted>('/ingestion/resolve')

export const getJob = (jobId: string) => apiGet<JobSnapshot>(`/ingestion/jobs/${jobId}`)
