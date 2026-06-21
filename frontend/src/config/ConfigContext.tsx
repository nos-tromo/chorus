import { createContext, useContext } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Spinner, Banner } from '@infra/ui'
import { fetchConfig } from '../api/config'
import type { AppConfig } from '../api/types'
import { catalogs, format } from '../i18n'
import type { Strings } from '../i18n'

const ConfigContext = createContext<AppConfig | null>(null)

export function ConfigProvider({ children }: { children: React.ReactNode }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['config'],
    queryFn: fetchConfig,
    staleTime: Infinity,
  })

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner label="Loading…" />
      </div>
    )
  }

  if (isError) {
    const message =
      error instanceof Error ? error.message : 'Failed to load application config.'
    return (
      <Banner variant="danger" className="m-4">
        {message}
      </Banner>
    )
  }

  return <ConfigContext.Provider value={data!}>{children}</ConfigContext.Provider>
}

export function useConfig(): AppConfig {
  const ctx = useContext(ConfigContext)
  if (ctx === null) {
    throw new Error('useConfig must be used inside <ConfigProvider>')
  }
  return ctx
}

export function useT(): (
  key: keyof Strings,
  vars?: Record<string, string | number>,
) => string {
  const config = useConfig()
  return (key, vars) => format(catalogs[config.language][key], vars)
}
