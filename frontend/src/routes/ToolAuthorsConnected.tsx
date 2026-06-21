import { useState, type FormEvent } from 'react'
import { Banner, Card, Spinner } from '@infra/ui'
import { useT } from '../config/ConfigContext'
import { useToolCall } from '../hooks/useToolCall'
import { EntityInput } from '../components/form/EntityInput'
import { LimitField } from '../components/form/LimitField'
import { SubmitButton } from '../components/form/SubmitButton'
import { DataTable } from '../components/DataTable'

// ── Types ─────────────────────────────────────────────────────────────────────

interface SeedAuthor {
  author_id: string
  handle: string | null
  display_name: string | null
}

interface ConnectedAuthor {
  author_id: string
  handle: string | null
  display_name: string | null
  overlap: number
  shared_topics: string[]
}

interface SeedConnections {
  seed: SeedAuthor
  connected: ConnectedAuthor[]
}

interface AuthorsConnectedByTopicOut {
  results: SeedConnections[]
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Derive display label: display_name → handle → author_id. */
function seedLabel(seed: SeedAuthor): string {
  return seed.display_name ?? seed.handle ?? seed.author_id
}

// ── Component ─────────────────────────────────────────────────────────────────

export function ToolAuthorsConnected() {
  const t = useT()
  const mutation = useToolCall<AuthorsConnectedByTopicOut>('authors_connected_by_topic')

  const [seedAuthor, setSeedAuthor] = useState('')
  const [minOverlap, setMinOverlap] = useState(1)
  const [limit, setLimit] = useState(50)

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    mutation.mutate({ seed_author: seedAuthor, min_overlap: minOverlap, limit })
  }

  const result = mutation.data ?? null
  const groups = result?.results ?? null
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
        <h1 className="text-2xl font-semibold">{t('authors_connected.title')}</h1>
        <p className="text-sm text-muted-foreground mt-1">
          {t('authors_connected.caption')}
        </p>
      </div>

      {/* Form */}
      <form onSubmit={handleSubmit} className="space-y-4 max-w-lg">
        <EntityInput
          label={t('authors_connected.seed_author_input')}
          value={seedAuthor}
          onChange={setSeedAuthor}
          required
        />
        <LimitField
          label={t('authors_connected.min_overlap')}
          min={1}
          max={50}
          value={minOverlap}
          onChange={setMinOverlap}
        />
        <LimitField
          label={t('authors_connected.limit')}
          min={1}
          max={200}
          value={limit}
          onChange={setLimit}
        />
        <SubmitButton loading={mutation.isPending} disabled={!seedAuthor}>
          {t('authors_connected.find')}
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
      {groups !== null && !mutation.isPending && (
        <div className="space-y-6">
          {groups.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t('authors_connected.no_seed')}
            </p>
          ) : (
            groups.map((group) => {
              const label = seedLabel(group.seed)
              const connected = group.connected

              return (
                <Card key={group.seed.author_id} className="p-4 space-y-3">
                  <h2 className="text-lg font-semibold">
                    {t('authors_connected.connected_count', {
                      label,
                      n: connected.length,
                    })}
                  </h2>
                  {connected.length > 0 ? (
                    <DataTable
                      rows={connected as unknown as Record<string, unknown>[]}
                      empty=""
                    />
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      {t('authors_connected.none')}
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
