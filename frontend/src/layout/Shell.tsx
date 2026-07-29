import type { ReactNode } from 'react'
import { AppHeader } from '@infra/ui'
import { Sidebar } from './Sidebar'
import { useT } from '../config/ConfigContext'

export function Shell({ children }: { children: ReactNode }) {
  const t = useT()
  return (
    <div className="min-h-screen flex flex-col bg-background text-foreground">
      <AppHeader
        title="chorus"
        // No identity source: X-Auth-User is a trusted header the edge
        // gateway injects server-side for the backend only, never surfaced
        // to the SPA — the header hides the user block when undefined.
        user={undefined}
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
