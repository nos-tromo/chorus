import { Card, Banner, Spinner, Badge } from '@infra/ui'
import { useHealth } from '../hooks/useHealth'
import { useTools } from '../hooks/useTools'
import { useStats } from '../hooks/useStats'
import { useConfig, useT } from '../config/ConfigContext'
import { StatChart } from '../components/StatChart'

/** Format an ISO timestamp to a human-readable local string, or return a dash. */
function formatTs(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

/** Compute resolution coverage as a percentage string, or '—' when denominator is 0. */
function resolutionPct(resolved: number, total: number): string {
  if (total === 0) return '—'
  return `${Math.round((resolved / total) * 100)} %`
}

interface KpiTileProps {
  label: string
  value: number | string
  testId?: string
}

function KpiTile({ label, value, testId }: KpiTileProps) {
  return (
    <div
      className="flex flex-col gap-1 rounded-md border border-border bg-muted/30 p-3"
      {...(testId ? { 'data-testid': testId } : {})}
    >
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-xl font-semibold tabular-nums">{value}</span>
    </div>
  )
}

export function Landing() {
  const t = useT()
  const { ingestion_enabled } = useConfig()
  const health = useHealth()
  const tools = useTools()
  const stats = useStats()

  return (
    <div className="p-8 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">chorus</h1>
        <p className="text-sm text-muted-foreground mt-1">{t('landing.caption')}</p>
      </div>

      {/* Ingestion status line */}
      <p className="text-sm">
        {ingestion_enabled ? t('landing.ingestion_on') : t('landing.ingestion_off')}
      </p>

      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
        {/* Backend health */}
        <Card className="p-4 space-y-3">
          <h2 className="text-base font-medium">{t('landing.backend_health')}</h2>
          {health.isLoading && <Spinner label="…" />}
          {health.isError && (
            <Banner variant="danger">
              {t('common.unreachable', {
                error:
                  health.error instanceof Error
                    ? health.error.message
                    : String(health.error),
              })}
            </Banner>
          )}
          {health.isSuccess && (
            <Badge variant="accent" data-testid="health-ok">
              {health.data.status}
            </Badge>
          )}
        </Card>

        {/* Registered tools */}
        <Card className="p-4 space-y-3">
          <h2 className="text-base font-medium">{t('landing.registered_tools')}</h2>
          {tools.isLoading && <Spinner label="…" />}
          {tools.isError && (
            <Banner variant="danger">
              {t('common.unreachable', {
                error:
                  tools.error instanceof Error
                    ? tools.error.message
                    : String(tools.error),
              })}
            </Banner>
          )}
          {tools.isSuccess && tools.data.length > 0 && (
            <ul className="space-y-2 text-sm">
              {tools.data.map((tool) => (
                <li key={tool.name}>
                  <span className="font-mono font-medium">{tool.name}</span>
                  {tool.description && (
                    <span className="text-muted-foreground ml-2">— {tool.description}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* ── Graph diagnostics dashboard ── */}
      <section className="space-y-4">
        <h2 className="text-base font-medium">{t('dashboard.graph_overview')}</h2>

        {stats.isLoading && (
          <div data-testid="stats-spinner">
            <Spinner label={t('dashboard.loading')} />
          </div>
        )}

        {stats.isError && (
          <Banner variant="danger">
            {t('common.unreachable', {
              error:
                stats.error instanceof Error
                  ? stats.error.message
                  : String(stats.error),
            })}
          </Banner>
        )}

        {stats.isSuccess && stats.data.counts.posts === 0 && (
          <p
            data-testid="stats-no-data"
            className="text-sm text-muted-foreground"
          >
            {t('dashboard.no_data')}
          </p>
        )}

        {stats.isSuccess && stats.data.counts.posts > 0 && (
          <div className="space-y-4">
            {/* Node KPIs */}
            <div>
              <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">
                {t('dashboard.nodes')}
              </h3>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
                <KpiTile label={t('dashboard.posts')} value={stats.data.counts.posts} testId="kpi-posts" />
                <KpiTile label={t('dashboard.authors')} value={stats.data.counts.authors} testId="kpi-authors" />
                <KpiTile label={t('dashboard.entities')} value={stats.data.counts.entities} testId="kpi-entities" />
                <KpiTile label={t('dashboard.hashtags')} value={stats.data.counts.hashtags} testId="kpi-hashtags" />
                <KpiTile label={t('dashboard.groups')} value={stats.data.counts.groups} testId="kpi-groups" />
              </div>
            </div>

            {/* Edge KPIs */}
            <div>
              <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">
                {t('dashboard.edges')}
              </h3>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                <KpiTile label={t('dashboard.mentions')} value={stats.data.edges.mentions} testId="kpi-mentions" />
                <KpiTile label={t('dashboard.follows')} value={stats.data.edges.follows} testId="kpi-follows" />
                <KpiTile label={t('dashboard.friends')} value={stats.data.edges.friends} testId="kpi-friends" />
              </div>
            </div>

            {/* Named highlights + health row */}
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {/* Top entities */}
              <Card className="p-4 space-y-2">
                <h3 className="text-sm font-medium">{t('dashboard.top_entities')}</h3>
                {stats.data.top_entities.length === 0 ? (
                  <p className="text-xs text-muted-foreground">—</p>
                ) : (
                  <ul className="space-y-1 text-sm">
                    {stats.data.top_entities.map((e) => (
                      <li key={e.name} className="flex justify-between gap-2">
                        <span className="truncate">{e.name}</span>
                        <span className="tabular-nums text-muted-foreground">{e.count}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>

              {/* Top authors */}
              <Card className="p-4 space-y-2">
                <h3 className="text-sm font-medium">{t('dashboard.top_authors')}</h3>
                {stats.data.top_authors.length === 0 ? (
                  <p className="text-xs text-muted-foreground">—</p>
                ) : (
                  <ul className="space-y-1 text-sm">
                    {stats.data.top_authors.map((a) => (
                      <li key={a.author_id} className="flex justify-between gap-2">
                        <span className="truncate">{a.label}</span>
                        <span className="tabular-nums text-muted-foreground">{a.count}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>

              {/* Health KPIs */}
              <Card className="p-4 space-y-3">
                <h3 className="text-sm font-medium">{t('dashboard.health')}</h3>
                <div className="space-y-2 text-sm">
                  <div>
                    <span className="text-xs text-muted-foreground block">{t('dashboard.latest_ingestion')}</span>
                    <span data-testid="latest-ingestion" className="tabular-nums">
                      {formatTs(stats.data.latest_ingested_at)}
                    </span>
                  </div>
                  <div>
                    <span className="text-xs text-muted-foreground block">{t('dashboard.resolution_coverage')}</span>
                    <span data-testid="resolution-coverage" className="tabular-nums">
                      {resolutionPct(
                        stats.data.resolution.resolved_aliases,
                        stats.data.resolution.total_aliases,
                      )}
                    </span>
                  </div>
                </div>
              </Card>

              {/* Posts by platform chart */}
              <Card className="p-4 space-y-2">
                <h3 className="text-sm font-medium">{t('dashboard.posts_by_platform')}</h3>
                <StatChart
                  data={stats.data.posts_by_platform}
                  noDataLabel={t('dashboard.no_chart_data')}
                />
              </Card>
            </div>
          </div>
        )}
      </section>

      {/* Pick a tool hint */}
      <p className="text-sm text-muted-foreground">{t('landing.pick_tool')}</p>
    </div>
  )
}
