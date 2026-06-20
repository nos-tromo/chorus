import { useState, useCallback } from 'react'

export interface ToolFormHandle<T extends Record<string, unknown>> {
  values: T
  set: <K extends keyof T>(key: K, value: T[K]) => void
  reset: () => void
}

export function useToolForm<T extends Record<string, unknown>>(
  initial: T,
): ToolFormHandle<T> {
  const [values, setValues] = useState<T>(initial)

  const set = useCallback(<K extends keyof T>(key: K, value: T[K]) => {
    setValues((prev) => ({ ...prev, [key]: value }))
  }, [])

  const reset = useCallback(() => {
    setValues(initial)
  }, [initial])

  return { values, set, reset }
}
