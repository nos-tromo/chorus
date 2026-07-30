import type { ReactNode } from 'react'
import { AppHeader } from '@infra/ui'
import { Sidebar } from './Sidebar'
import { useConfig, useT } from '../config/ConfigContext'
import { useWhoami } from '../hooks/useWhoami'

export function Shell({ children }: { children: ReactNode }) {
  const config = useConfig()
  const t = useT()
  const { data: whoami } = useWhoami()
  return (
    <div className="min-h-screen flex flex-col bg-background text-foreground">
      <AppHeader
        title="chorus"
        user={whoami?.display_name ?? whoami?.username}
        // GET /config already carries the release version for i18n bootstrap;
        // reuse it here rather than a second network round trip.
        version={config.version ? `v${config.version}` : undefined}
        homeLabel={t('app.header.home')}
        themeLabels={{
          system: t('app.header.theme.system'),
          light: t('app.header.theme.light'),
          dark: t('app.header.theme.dark'),
        }}
      />
      <div className="flex flex-1 min-h-0">
        <Sidebar />
        <main className="flex-1 min-w-0 overflow-auto">{children}</main>
      </div>
    </div>
  )
}
