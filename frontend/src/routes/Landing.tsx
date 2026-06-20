import { Card, Banner, Spinner, Badge } from '@infra/ui'
import { useHealth } from '../hooks/useHealth'
import { useTools } from '../hooks/useTools'
import { useConfig, useT } from '../config/ConfigContext'

export function Landing() {
  const t = useT()
  const { ingestion_enabled } = useConfig()
  const health = useHealth()
  const tools = useTools()

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

      {/* Pick a tool hint */}
      <p className="text-sm text-muted-foreground">{t('landing.pick_tool')}</p>
    </div>
  )
}
