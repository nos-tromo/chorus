import { useT } from '../config/ConfigContext'
import type { AgentTraceEntry } from '../api/types'

interface ToolTraceProps {
  trace: AgentTraceEntry[]
}

export function ToolTrace({ trace }: ToolTraceProps) {
  const t = useT()

  if (trace.length === 0) return null

  return (
    <details className="mt-2 text-sm">
      <summary className="cursor-pointer text-muted-foreground select-none">
        {t('agent.tool_calls', { n: trace.length })}
      </summary>
      <ul className="mt-2 space-y-3 pl-2 border-l-2 border-border">
        {trace.map((entry, idx) => (
          <li key={idx} className="space-y-1">
            {entry.error ? (
              // Three safe text nodes — no dangerouslySetInnerHTML.
              // entry.tool and entry.error are rendered as text, never as HTML.
              <p className="text-destructive">
                <strong>{entry.tool}</strong>
                {' '}
                {t('agent.trace_error_label')}
                {' '}
                {entry.error}
              </p>
            ) : (
              <p className="font-medium">
                <span className="font-mono">{entry.tool}</span>
                {entry.result_count !== null && (
                  <span className="text-muted-foreground font-normal">
                    {t('agent.trace_results', { count: entry.result_count })}
                  </span>
                )}
              </p>
            )}
            <pre className="text-xs bg-muted rounded px-2 py-1 overflow-x-auto whitespace-pre-wrap">
              {JSON.stringify(entry.arguments, null, 2)}
            </pre>
          </li>
        ))}
      </ul>
    </details>
  )
}
