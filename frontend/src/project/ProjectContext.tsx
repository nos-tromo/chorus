import { createContext, Fragment, useCallback, useContext, useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Banner, Button, Card, Spinner } from '@infra/ui'
import { setActiveProjectHeader } from '../api/client'
import { describeError } from '../api/errorMessage'
import { useT } from '../config/ConfigContext'
import { useWhoami } from '../hooks/useWhoami'
import type { Whoami } from '../api/types'

/** Where the last-used project is remembered across reloads. */
export const PROJECT_STORAGE_KEY = 'chorus.activeProject'

/** Query keys that survive a project switch: identity and app config are
 *  session-scoped, everything else in the cache is one project's data. */
const SESSION_QUERY_KEYS = ['config', 'whoami']

interface ProjectState {
  /** Projects the caller may access, in configured order. */
  projects: string[]
  /** The project every request currently selects. */
  active: string
  /** Switch projects: re-points the header and drops the outgoing project's data. */
  setActive: (project: string) => void
}

const ProjectContext = createContext<ProjectState | null>(null)

/** The project to open with, or null when the user has to choose.
 *  A remembered choice outranks the server's, but both have to still be
 *  allowed — a claim can shrink between sessions. */
function openingProject(whoami: Whoami, remembered: string | null): string | null {
  if (remembered && whoami.projects.includes(remembered)) return remembered
  if (whoami.active_project && whoami.projects.includes(whoami.active_project)) {
    return whoami.active_project
  }
  return null
}

/**
 * Resolves the active project before any project-scoped request goes out
 * (ADR 0017).
 *
 * `/whoami` is the only source for the project list, and it resolves the
 * project leniently, so bootstrapping is a two-beat affair: ask who we
 * are, then either open the obvious project or prompt for one. Children
 * render only once a project is selected — the header is set first, so no
 * child query can fire unscoped.
 */
export function ProjectProvider({ children }: { children: React.ReactNode }) {
  const t = useT()
  const queryClient = useQueryClient()
  const { data, isLoading, isError, error } = useWhoami()
  const [active, setActiveState] = useState<string | null>(null)

  const select = useCallback((project: string) => {
    setActiveProjectHeader(project)
    localStorage.setItem(PROJECT_STORAGE_KEY, project)
    setActiveState(project)
  }, [])

  useEffect(() => {
    if (!data || active !== null) return
    const remembered = localStorage.getItem(PROJECT_STORAGE_KEY)
    if (remembered && !data.projects.includes(remembered)) {
      localStorage.removeItem(PROJECT_STORAGE_KEY)
    }
    const opening = openingProject(data, remembered)
    if (opening) select(opening)
  }, [data, active, select])

  const setActive = useCallback(
    (project: string) => {
      if (project === active) return
      void queryClient.cancelQueries()
      queryClient.removeQueries({
        predicate: (query) => !SESSION_QUERY_KEYS.includes(query.queryKey[0] as string),
      })
      select(project)
    },
    [active, queryClient, select],
  )

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner label={t('project.loading')} />
      </div>
    )
  }

  // Shell tolerates a failed /whoami by dropping the user menu, but there
  // is no project to scope requests to, so the app itself cannot open.
  if (isError || !data) {
    const descriptor = describeError(error)
    return (
      <Banner variant="danger" className="m-4">
        {t(descriptor.key, descriptor.vars)}
      </Banner>
    )
  }

  if (data.projects.length === 0) {
    return (
      <Banner variant="danger" className="m-4">
        {t('project.no_access')}
      </Banner>
    )
  }

  if (active === null) {
    // Either the opening project is a beat away (the effect above runs on
    // the next tick) or there genuinely is a choice to make. Showing the
    // spinner in the first case keeps the prompt from flashing past.
    if (openingProject(data, localStorage.getItem(PROJECT_STORAGE_KEY))) {
      return (
        <div className="flex h-screen items-center justify-center">
          <Spinner label={t('project.loading')} />
        </div>
      )
    }
    return (
      <div className="flex h-screen items-center justify-center p-4">
        <Card className="flex w-full max-w-sm flex-col gap-4 p-6">
          <div>
            <h1 className="text-lg font-semibold">{t('project.pick_title')}</h1>
            <p className="text-sm text-muted-foreground">{t('project.pick_hint')}</p>
          </div>
          <div className="flex flex-col gap-2">
            {data.projects.map((project) => (
              <Button key={project} onClick={() => select(project)}>
                {project}
              </Button>
            ))}
          </div>
        </Card>
      </div>
    )
  }

  return (
    <ProjectContext.Provider value={{ projects: data.projects, active, setActive }}>
      {children}
    </ProjectContext.Provider>
  )
}

export function useProject(): ProjectState {
  const ctx = useContext(ProjectContext)
  if (ctx === null) {
    throw new Error('useProject must be used inside <ProjectProvider>')
  }
  return ctx
}

/** Remount boundary for project data. Everything below it is rebuilt on a
 *  switch, so accumulated component state — explorer graphs, agent
 *  conversations, in-flight job polls — cannot outlive its project. */
export function ProjectScoped({ children }: { children: React.ReactNode }) {
  const { active } = useProject()
  return <Fragment key={active}>{children}</Fragment>
}
