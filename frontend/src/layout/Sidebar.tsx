import { NavLink } from 'react-router-dom'
import { useConfig, useT } from '../config/ConfigContext'
import { VersionBadge } from '../components/VersionBadge'
import type { Strings } from '../i18n'

function navClass({ isActive }: { isActive: boolean }) {
  return [
    'block rounded-md px-3 py-1.5 text-sm hover:bg-zinc-800 transition-colors',
    isActive ? 'bg-primary/15 text-primary' : 'text-foreground',
  ].join(' ')
}

interface NavGroup {
  groupKey: keyof Strings
  items: Array<{ labelKey: keyof Strings; to: string }>
}

const NAV_GROUPS: NavGroup[] = [
  {
    groupKey: 'nav.group.entities',
    items: [
      { labelKey: 'posts.title', to: '/tools/posts-mentioning' },
      { labelKey: 'authors_mentioning.title', to: '/tools/authors-mentioning' },
    ],
  },
  {
    groupKey: 'nav.group.authors',
    items: [
      { labelKey: 'author_activity.title', to: '/tools/author-activity' },
      { labelKey: 'authors_connected.title', to: '/tools/authors-connected' },
    ],
  },
  {
    groupKey: 'nav.group.topics',
    items: [{ labelKey: 'topic_cooc.title', to: '/tools/topic-cooccurrence' }],
  },
  {
    groupKey: 'nav.group.networks',
    items: [{ labelKey: 'nav.explorer', to: '/tools/explorer' }],
  },
]

export function Sidebar() {
  const config = useConfig()
  const t = useT()

  return (
    <aside className="w-64 shrink-0 border-r border-border flex flex-col gap-4 bg-zinc-950 p-4">
      <h2 className="text-lg font-semibold tracking-tight">chorus</h2>

      <nav className="flex flex-col gap-1">
        {/* Top-level: Agent */}
        <NavLink to="/agent" className={navClass}>
          {t('agent.title')}
        </NavLink>
      </nav>

      {/* Grouped tool links */}
      {NAV_GROUPS.map((group) => (
        <nav key={group.groupKey} className="flex flex-col gap-1">
          <p className="px-3 text-[11px] uppercase tracking-wider text-muted-foreground">
            {t(group.groupKey)}
          </p>
          {group.items.map(({ labelKey, to }) => (
            <NavLink key={to} to={to} className={navClass}>
              {t(labelKey)}
            </NavLink>
          ))}
        </nav>
      ))}

      {/* Conditional: Ingestion */}
      {config.ingestion_enabled && (
        <nav className="flex flex-col gap-1 border-t border-border pt-4">
          <NavLink to="/ingestion" className={navClass}>
            {t('ingest.title')}
          </NavLink>
        </nav>
      )}

      <div className="mt-auto pt-4">
        <VersionBadge />
      </div>
    </aside>
  )
}
