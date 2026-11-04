import type { ReactNode } from 'react'
import { AppShell, PurposeBar, type PurposeBarLabels } from '@infra/ui'
import { Sidebar } from './Sidebar'
import { PURPOSE_SCOPE } from '../api/purpose'
import { useConfig, useT } from '../config/ConfigContext'
import { useRefetchOnPurpose } from '../hooks/useRefetchOnPurpose'
import { useWhoami } from '../hooks/useWhoami'

/** Typed in full, so a label added to `@infra/ui` fails the build until it is translated. */
function purposeLabels(t: ReturnType<typeof useT>): PurposeBarLabels {
  return {
    purpose: t('purpose.label'),
    empty: t('purpose.empty'),
    required: t('purpose.required'),
    title: t('purpose.title'),
    description: t('purpose.description'),
    requested: t('purpose.requested'),
    caseRef: t('purpose.case_ref'),
    reason: t('purpose.reason'),
    save: t('purpose.save'),
    cancel: t('purpose.cancel'),
    clear: t('purpose.clear'),
    errorLength: t('purpose.error_length'),
    errorControl: t('purpose.error_control'),
    errorHeader: t('purpose.error_header'),
    errorEmpty: t('purpose.error_empty'),
  }
}

export function Shell({ children }: { children: ReactNode }) {
  const config = useConfig()
  const t = useT()
  const { data: whoami } = useWhoami()
  useRefetchOnPurpose()
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
      headerActions={
        <PurposeBar
          scope={PURPOSE_SCOPE}
          required={config.require_purpose.length > 0}
          labels={purposeLabels(t)}
        />
      }
      sidebar={<Sidebar />}
    >
      {children}
    </AppShell>
  )
}
