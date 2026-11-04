import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { PURPOSE_REQUIRED_STATUS, subscribePurpose } from '@infra/ui'
import { ApiError } from '../api/client'
import { PURPOSE_SCOPE } from '../api/purpose'

/**
 * Once a purpose is stated, reload the queries the backend refused for want of
 * one (a 428 whose prompt was declined — the dashboard's /stats on first
 * visit), so they recover without a page reload. Failed mutations stay
 * failed: resubmitting a form is the user's call.
 */
export function useRefetchOnPurpose(): void {
  const queryClient = useQueryClient()
  useEffect(
    () =>
      subscribePurpose(PURPOSE_SCOPE, (purpose) => {
        if (!purpose) return
        void queryClient.invalidateQueries({
          predicate: (query) =>
            query.state.error instanceof ApiError &&
            query.state.error.status === PURPOSE_REQUIRED_STATUS,
        })
      }),
    [queryClient],
  )
}
