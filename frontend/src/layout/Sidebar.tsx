import { NavLink } from 'react-router-dom'
import { Select, SidebarGroup } from '@infra/ui'
import { useConfig, useT } from '../config/ConfigContext'
import { useProject } from '../project/ProjectContext'
import type { Strings } from '../i18n'

function navClass({ isActive }: { isActive: boolean }) {
  return [
    'block rounded-md px-3 py-1.5 text-sm hover:bg-muted transition-colors',
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
  const { projects, active, setActive } = useProject()

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      {/* AppShell's header already renders the app title — no second one here. */}

      {/* Only worth the space when there is somewhere to switch to; a
          single-project deployment never sees it (ADR 0017). */}
      {projects.length > 1 && (
        <div className="flex flex-col gap-1 border-b border-border pb-4">
          <label
            htmlFor="project-switcher"
            className="text-xs font-medium text-muted-foreground"
          >
            {t('project.switcher_label')}
          </label>
          <Select
            id="project-switcher"
            value={active}
            onChange={(e) => setActive(e.target.value)}
          >
            {projects.map((project) => (
              <option key={project} value={project}>
                {project}
              </option>
            ))}
          </Select>
        </div>
      )}

      <nav className="flex flex-col gap-1">
        {/* Top-level: Dashboard, Agent */}
        <NavLink to="/" end className={navClass}>
          {t('nav.dashboard')}
        </NavLink>
        <NavLink to="/agent" className={navClass}>
          {t('agent.title')}
        </NavLink>
      </nav>

      {/* Grouped tool links */}
      {NAV_GROUPS.map((group) => (
        <SidebarGroup key={group.groupKey} label={t(group.groupKey)}>
          {group.items.map(({ labelKey, to }) => (
            <NavLink key={to} to={to} className={navClass}>
              {t(labelKey)}
            </NavLink>
          ))}
        </SidebarGroup>
      ))}

      {/* Conditional: Ingestion */}
      {config.ingestion_enabled && (
        <nav className="flex flex-col gap-1 border-t border-border pt-4">
          <NavLink to="/ingestion" className={navClass}>
            {t('ingest.title')}
          </NavLink>
        </nav>
      )}
    </div>
  )
}
