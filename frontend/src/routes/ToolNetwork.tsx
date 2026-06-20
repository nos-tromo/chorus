/**
 * ToolNetwork — `network_around` tool screen.
 *
 * Bipartite author-topic network around a queried entity. Renders a Cytoscape
 * graph with cose layout. Form mirrors the Streamlit page (07_network_around.py).
 */

import { useState, type FormEvent } from 'react'
import { Banner, Spinner } from '@infra/ui'
import { useT } from '../config/ConfigContext'
import { useToolCall } from '../hooks/useToolCall'
import { EntityInput } from '../components/form/EntityInput'
import { LimitField } from '../components/form/LimitField'
import { SubmitButton } from '../components/form/SubmitButton'
import { GraphCanvas } from '../components/GraphCanvas'
import { toNetworkElements } from '../lib/networkElements'
import { networkStylesheet } from '../lib/graphStyles'
import type { NetworkAroundOut } from '../api/types'

const COSE_LAYOUT = { name: 'cose' } as const

export function ToolNetwork() {
  const t = useT()
  const mutation = useToolCall<NetworkAroundOut>('network_around')

  const [entity, setEntity] = useState('')
  const [depth, setDepth] = useState(2)
  const [limit, setLimit] = useState(25)
  const [topicLimit, setTopicLimit] = useState(50)

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    mutation.mutate({ entity, depth, limit, topic_limit: topicLimit })
  }

  const data = mutation.data
  const errorMessage =
    mutation.error instanceof Error
      ? mutation.error.message
      : mutation.error
        ? String(mutation.error)
        : ''

  const authorCount = data ? data.nodes.filter((n) => n.kind === 'author').length : 0
  const topicCount = data ? data.nodes.filter((n) => n.kind === 'topic').length : 0

  return (
    <div className="p-8 space-y-6">
      {/* Title + caption */}
      <div>
        <h1 className="text-2xl font-semibold">{t('network.title')}</h1>
        <p className="text-sm text-muted-foreground mt-1">{t('network.caption')}</p>
      </div>

      {/* Form */}
      <form onSubmit={handleSubmit} className="space-y-4 max-w-lg">
        <EntityInput
          label={t('common.entity_input')}
          value={entity}
          onChange={setEntity}
          required
        />
        <LimitField
          label={t('network.depth')}
          min={1}
          max={2}
          value={depth}
          onChange={setDepth}
        />
        <div className="grid grid-cols-2 gap-4">
          <LimitField
            label={t('network.author_limit')}
            min={1}
            max={200}
            value={limit}
            onChange={setLimit}
          />
          <LimitField
            label={t('network.topic_limit')}
            min={1}
            max={500}
            value={topicLimit}
            onChange={setTopicLimit}
          />
        </div>
        <SubmitButton loading={mutation.isPending} disabled={!entity}>
          {t('network.build')}
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
      {data && !mutation.isPending && (
        <div className="space-y-4">
          {data.nodes.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('network.empty')}</p>
          ) : (
            <>
              <p className="text-sm text-muted-foreground">
                {t('network.counts', {
                  n: data.nodes.length,
                  authors: authorCount,
                  topics: topicCount,
                  edges: data.edges.length,
                })}
              </p>
              {data.truncated && (
                <Banner variant="info">{t('network.capped')}</Banner>
              )}
              <GraphCanvas
                elements={toNetworkElements(data)}
                stylesheet={networkStylesheet}
                layout={COSE_LAYOUT}
              />
            </>
          )}
        </div>
      )}
    </div>
  )
}
