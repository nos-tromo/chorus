import { createContext, use } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Spinner, Banner } from '@infra/ui'
import { fetchConfig } from '../api/config'
import type { AppConfig } from '../api/types'
import { catalogs, format } from '../i18n'
import type { Strings } from '../i18n'

const ConfigContext = createContext<AppConfig | null>(null)

export function ConfigProvider({ children }: { children: React.ReactNode }) {
  const { data, isLoading, isError } = useQuery({
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
    // Renders before i18n boots (config load failed), so no catalog is
    // available yet — a static bilingual literal, never error.message.
    return (
      <Banner variant="danger" className="m-4">
        Service unreachable. / Dienst nicht erreichbar.
      </Banner>
    )
  }

  return <ConfigContext value={data!}>{children}</ConfigContext>
}

export function useConfig(): AppConfig {
  const ctx = use(ConfigContext)
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
