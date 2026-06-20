import { useMutation } from '@tanstack/react-query'
import { applyMigrations, startIngest, startResolve } from '../api/ingestion'
import type { JobAccepted } from '../api/types'

export function useApplyMigrations() {
  return useMutation<{ applied: string[] }, Error>({
    mutationFn: applyMigrations,
  })
}

export interface StartIngestVars {
  files: File[]
  since: string | undefined
  thenResolve: boolean
}

export function useStartIngest() {
  return useMutation<JobAccepted, Error, StartIngestVars>({
    mutationFn: ({ files, since, thenResolve }) => startIngest(files, since, thenResolve),
  })
}

export function useStartResolve() {
  return useMutation<JobAccepted, Error>({
    mutationFn: startResolve,
  })
}
