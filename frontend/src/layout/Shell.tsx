import type { ReactNode } from 'react'
import { AppShell } from '@infra/ui'
import { Sidebar } from './Sidebar'
import { useConfig, useT } from '../config/ConfigContext'
import { useWhoami } from '../hooks/useWhoami'

export function Shell({ children }: { children: ReactNode }) {
  const config = useConfig()
  const t = useT()
  const { data: whoami } = useWhoami()
  return (
    <AppShell
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
      signOutLabel={t('app.header.sign_out')}
      sidebar={<Sidebar />}
    >
      {children}
    </AppShell>
  )
}
