import { useState, type FormEvent } from 'react'
import { Banner, Card, Spinner } from '@infra/ui'
import { useT } from '../config/ConfigContext'
import { useToolCall } from '../hooks/useToolCall'
import { EntityInput } from '../components/form/EntityInput'
import { TimeRangeInputs, type TimeRangeValue } from '../components/form/TimeRangeInputs'
import { SubmitButton } from '../components/form/SubmitButton'
import { DataTable } from '../components/DataTable'

// ── Types ─────────────────────────────────────────────────────────────────────

interface TopicCount {
  topic: string
  entity_id: string | null
  count: number
}

interface AuthorSummary {
  author_id: string
  handle: string | null
  display_name: string | null
  platform: string | null
  post_count: number
  posting_count: number
  comment_count: number
  message_count: number
  first_activity: string | null
  last_activity: string | null
  expected_reactions_total: number
  collected_reactions_total: number
  expected_comments_total: number
  collected_comments_total: number
  top_topics: TopicCount[]
}

interface AuthorActivitySummaryOut {
  summaries: AuthorSummary[]
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Derive the display label: display_name → handle → author_id. */
function summaryLabel(su: AuthorSummary): string {
  return su.display_name ?? su.handle ?? su.author_id
}

/** Metric fields to surface in order (mirrors Streamlit st.json block). */
const METRIC_KEYS: (keyof AuthorSummary)[] = [
  'post_count',
  'posting_count',
  'comment_count',
  'message_count',
  'first_activity',
  'last_activity',
  'expected_reactions_total',
  'collected_reactions_total',
  'expected_comments_total',
  'collected_comments_total',
]

// ── Component ─────────────────────────────────────────────────────────────────

export function ToolAuthorActivity() {
  const t = useT()
  const mutation = useToolCall<AuthorActivitySummaryOut>('author_activity_summary')

  const [author, setAuthor] = useState('')
  const [timeRange, setTimeRange] = useState<TimeRangeValue>({})

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const payload: Record<string, unknown> = { author }
    if (timeRange.from) payload['from'] = timeRange.from
    if (timeRange.to) payload['to'] = timeRange.to
    mutation.mutate(payload)
  }

  const result = mutation.data ?? null
  const summaries = result?.summaries ?? null
  const errorMessage =
    mutation.error instanceof Error
      ? mutation.error.message
      : mutation.error
        ? String(mutation.error)
        : ''

  return (
    <div className="p-8 space-y-6">
      {/* Title */}
      <div>
        <h1 className="text-2xl font-semibold">{t('author_activity.title')}</h1>
        <p className="text-sm text-muted-foreground mt-1">
          {t('common.resolution_note')}
        </p>
      </div>

      {/* Form */}
      <form onSubmit={handleSubmit} className="space-y-4 max-w-lg">
        <EntityInput
          label={t('author_activity.author_input')}
          value={author}
          onChange={setAuthor}
          required
        />
        <TimeRangeInputs
          fromLabel={t('common.from_ts')}
          toLabel={t('common.to_ts')}
          value={timeRange}
          onChange={setTimeRange}
        />
        <SubmitButton loading={mutation.isPending} disabled={!author}>
          {t('author_activity.summarize')}
        </SubmitButton>
      </form>

      {/* Error */}
      {mutation.isError && (
        <Banner variant="danger">
          {t('common.tool_call_failed', { error: errorMessage })}
        </Banner>
      )}

      {/* Loading */}
      {mutation.isPending && <Spinner label="…" />}

      {/* Results */}
      {summaries !== null && !mutation.isPending && (
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            {t('author_activity.matched', { n: summaries.length })}
          </p>

          {summaries.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t('author_activity.no_author')}
            </p>
          ) : (
            summaries.map((su) => {
              const label = summaryLabel(su)
              const metrics = Object.fromEntries(
                METRIC_KEYS.map((k) => [k, su[k] ?? null]),
              ) as Record<string, unknown>

              return (
                <Card key={su.author_id} className="p-4 space-y-3">
                  <h2 className="text-lg font-semibold">
                    {label} · {su.author_id}
                  </h2>

                  {/* Activity metrics table */}
                  <DataTable
                    rows={[metrics]}
                    empty=""
                  />

                  {/* Top topics */}
                  {su.top_topics.length > 0 ? (
                    <DataTable
                      rows={su.top_topics as unknown as Record<string, unknown>[]}
                      empty=""
                    />
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      {t('author_activity.no_topics')}
                    </p>
                  )}
                </Card>
              )
            })
          )}
        </div>
      )}
    </div>
  )
}
