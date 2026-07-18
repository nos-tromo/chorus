/**
 * ToolNetwork — `network_around` tool screen.
 *
 * Bipartite author-topic network around a queried entity. Renders a
 * reactive, incrementally-expandable ForceGraph. Form mirrors the Streamlit
 * page (07_network_around.py).
 */

import { useMemo, useRef, useState, type FormEvent } from 'react'
import { Banner, Button, ForceGraph, Spinner, type ForceGraphHandle } from '@infra/ui'
import { useT } from '../config/ConfigContext'
import { useToolCall } from '../hooks/useToolCall'
import { useNetworkExplorer } from '../hooks/useGraphExplorer'
import { EntityInput } from '../components/form/EntityInput'
import { LimitField } from '../components/form/LimitField'
import { SubmitButton } from '../components/form/SubmitButton'
import { NETWORK_NODE_STYLES, toNetworkForceGraph } from '../lib/networkElements'
import { downloadText, toGraphHtml, toGraphJson, toGraphML } from '../lib/graphExport'
import type { NetworkAroundOut } from '../api/types'

const EXPAND_LIMIT = 50

export function ToolNetwork() {
  const t = useT()
  const mutation = useToolCall<NetworkAroundOut>('network_around')
  const explorer = useNetworkExplorer()
  const apiRef = useRef<ForceGraphHandle | null>(null)

  const [entity, setEntity] = useState('')
  const [depth, setDepth] = useState(2)
  const [limit, setLimit] = useState(25)
  const [topicLimit, setTopicLimit] = useState(50)

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    mutation.mutate(
      { entity, depth, limit, topic_limit: topicLimit },
      { onSuccess: (out) => explorer.seedFrom(out) },
    )
  }

  const errorMessage =
    mutation.error instanceof Error
      ? mutation.error.message
      : mutation.error
        ? String(mutation.error)
        : ''

  const authorCount = explorer.graph
    ? explorer.graph.nodes.filter((n) => n.kind === 'author').length
    : 0
  const topicCount = explorer.graph
    ? explorer.graph.nodes.filter((n) => n.kind === 'topic').length
    : 0

  const fg = useMemo(
    () => (explorer.graph ? toNetworkForceGraph(explorer.graph) : null),
    [explorer.graph],
  )

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
      {fg && explorer.graph && (
        explorer.graph.nodes.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('network.empty')}</p>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <p className="text-sm text-muted-foreground">
                {t('network.counts', {
                  n: explorer.graph.nodes.length,
                  authors: authorCount,
                  topics: topicCount,
                  edges: explorer.graph.edges.length,
                })}
              </p>
              <Button
                type="button"
                variant="secondary"
                onClick={() =>
                  downloadText(
                    'chorus-network.json',
                    toGraphJson(fg.nodes, fg.edges),
                    'application/json',
                  )
                }
              >
                {t('graph.export_json')}
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={() =>
                  downloadText(
                    'chorus-network.graphml',
                    toGraphML(fg.nodes, fg.edges),
                    'application/xml',
                  )
                }
              >
                {t('graph.export_graphml')}
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={() =>
                  downloadText(
                    'chorus-network.html',
                    toGraphHtml({
                      title: `${t('network.title')} — ${entity}`,
                      nodes: fg.nodes,
                      edges: fg.edges,
                      positions: apiRef.current?.getPositions() ?? {},
                      nodeStyles: NETWORK_NODE_STYLES,
                      legend: [
                        { kind: 'seed', label: t('network.legend_seed') },
                        { kind: 'author', label: t('network.legend_author') },
                        { kind: 'topic', label: t('network.legend_topic') },
                      ],
                    }),
                    'text/html',
                  )
                }
              >
                {t('graph.export_html')}
              </Button>
            </div>
            {mutation.data?.truncated && <Banner variant="info">{t('network.capped')}</Banner>}
            {explorer.expansionTruncated && (
              <Banner variant="info">
                {t('graph.expansion_capped', { limit: EXPAND_LIMIT })}
              </Banner>
            )}
            {explorer.expandError && (
              <Banner variant="danger">
                {t('graph.expand_failed', { error: explorer.expandError })}
              </Banner>
            )}
            <ForceGraph
              apiRef={apiRef}
              nodes={fg.nodes}
              edges={fg.edges}
              nodeStyles={NETWORK_NODE_STYLES}
              selectedIds={explorer.selectedIds}
              onSelectionChange={explorer.select}
              onExpandNode={explorer.expand}
              onDeleteNodes={explorer.removeNodes}
              expandingId={explorer.expandingId}
              statusText={t('graph.hint')}
              legend={[
                { kind: 'seed', label: t('network.legend_seed') },
                { kind: 'author', label: t('network.legend_author') },
                { kind: 'topic', label: t('network.legend_topic') },
              ]}
              labels={{
                minEdges: t('graph.min_edges'),
                edgeLength: t('graph.edge_length'),
                zoom: t('graph.zoom'),
                reset: t('graph.reset'),
                fit: t('graph.fit'),
                expandSelected: t('graph.expand_node'),
                removeSelected: t('graph.remove_node'),
                removeSelectedMany: t('graph.remove_nodes'),
                maximize: t('graph.maximize'),
                minimize: t('graph.minimize'),
              }}
            />
          </div>
        )
      )}
    </div>
  )
}
