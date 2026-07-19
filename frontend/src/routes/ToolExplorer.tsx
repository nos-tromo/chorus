/**
 * ToolExplorer — unified graph explorer screen.
 *
 * One growing canvas over both `network_around` (author/topic mentions) and
 * `social_network_around` (author follows/friends) seeds, expandable via
 * `expand_network_node` / `expand_social_node`. The segmented control picks
 * which tool a submit seeds from, and `useUnifiedExplorer` keeps one
 * node/edge set across both families.
 */

import { useMemo, useRef, useState, type FormEvent } from 'react'
import { Banner, Button, ForceGraph, Spinner, type ForceGraphHandle } from '@infra/ui'
import { useT } from '../config/ConfigContext'
import { useToolCall } from '../hooks/useToolCall'
import { useUnifiedExplorer } from '../hooks/useUnifiedExplorer'
import { EntityInput } from '../components/form/EntityInput'
import { LimitField } from '../components/form/LimitField'
import { SubmitButton } from '../components/form/SubmitButton'
import { EXPLORER_NODE_STYLES, EXPLORER_EDGE_STYLES, toExplorerForceGraph } from '../lib/explorerElements'
import { computeExpandActions, dispatchExpandAction } from '../lib/explorerActions'
import { downloadText, toGraphHtml, toGraphJson, toGraphML } from '../lib/graphExport'
import type { NetworkAroundOut, SocialNetworkAroundOut } from '../api/types'

const EXPAND_LIMIT = 50

type SeedType = 'entity' | 'author'

export function ToolExplorer() {
  const t = useT()
  const networkMutation = useToolCall<NetworkAroundOut>('network_around')
  const socialMutation = useToolCall<SocialNetworkAroundOut>('social_network_around')
  const explorer = useUnifiedExplorer()
  const apiRef = useRef<ForceGraphHandle | null>(null)

  const [seedType, setSeedType] = useState<SeedType>('entity')
  const [entity, setEntity] = useState('')
  const [author, setAuthor] = useState('')
  const [depth, setDepth] = useState(2)
  const [limit, setLimit] = useState(25)
  const [secondaryLimit, setSecondaryLimit] = useState(50)
  const [seededVia, setSeededVia] = useState<SeedType | null>(null)

  const mutation = seedType === 'entity' ? networkMutation : socialMutation
  const seededMutation =
    seededVia === 'entity' ? networkMutation : seededVia === 'author' ? socialMutation : null

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (seedType === 'entity') {
      networkMutation.mutate(
        { entity, depth, limit, topic_limit: secondaryLimit },
        {
          onSuccess: (out) => {
            explorer.seedFromNetwork(out)
            setSeededVia('entity')
          },
        },
      )
    } else {
      socialMutation.mutate(
        { author, depth, limit, second_ring_limit: secondaryLimit },
        {
          onSuccess: (out) => {
            explorer.seedFromSocial(out)
            setSeededVia('author')
          },
        },
      )
    }
  }

  const errorMessage =
    seededMutation?.error instanceof Error
      ? seededMutation.error.message
      : seededMutation?.error
        ? String(seededMutation.error)
        : ''

  const fg = useMemo(
    () => (explorer.graph ? toExplorerForceGraph(explorer.graph) : null),
    [explorer.graph],
  )

  const selectedNode =
    explorer.selectedIds.length === 1
      ? (explorer.graph?.nodes.find((n) => n.id === explorer.selectedIds[0]) ?? null)
      : null
  const expandActions = computeExpandActions(selectedNode, t)
  const onExpandAction = (actionId: string, nodeId: string) =>
    dispatchExpandAction(explorer, actionId, nodeId)

  return (
    <div className="p-8 space-y-6">
      {/* Title + caption */}
      <div>
        <h1 className="text-2xl font-semibold">{t('explorer.title')}</h1>
        <p className="text-sm text-muted-foreground mt-1">{t('explorer.caption')}</p>
      </div>

      {/* Form */}
      <form onSubmit={handleSubmit} className="space-y-4 max-w-lg">
        <div className="flex gap-2" role="group" aria-label={t('explorer.title')}>
          <Button
            type="button"
            variant={seedType === 'entity' ? 'primary' : 'secondary'}
            aria-pressed={seedType === 'entity'}
            onClick={() => setSeedType('entity')}
          >
            {t('explorer.seed_entity')}
          </Button>
          <Button
            type="button"
            variant={seedType === 'author' ? 'primary' : 'secondary'}
            aria-pressed={seedType === 'author'}
            onClick={() => setSeedType('author')}
          >
            {t('explorer.seed_author')}
          </Button>
        </div>

        {seedType === 'entity' ? (
          <EntityInput
            label={t('common.entity_input')}
            value={entity}
            onChange={setEntity}
            required
          />
        ) : (
          <EntityInput
            label={t('social.author_input')}
            value={author}
            onChange={setAuthor}
            required
          />
        )}

        <LimitField
          label={seedType === 'entity' ? t('network.depth') : t('social.depth')}
          min={1}
          max={2}
          value={depth}
          onChange={setDepth}
        />
        <div className="grid grid-cols-2 gap-4">
          <LimitField
            label={seedType === 'entity' ? t('network.author_limit') : t('social.limit')}
            min={1}
            max={200}
            value={limit}
            onChange={setLimit}
          />
          <LimitField
            label={
              seedType === 'entity' ? t('network.topic_limit') : t('social.second_ring_limit')
            }
            min={1}
            max={500}
            value={secondaryLimit}
            onChange={setSecondaryLimit}
          />
        </div>
        <SubmitButton loading={mutation.isPending} disabled={seedType === 'entity' ? !entity : !author}>
          {t('explorer.build')}
        </SubmitButton>
      </form>

      {/* Error */}
      {seededMutation?.isError && (
        <Banner variant="danger">
          {t('common.tool_call_failed', { error: errorMessage })}
        </Banner>
      )}

      {/* Loading */}
      {mutation.isPending && <Spinner label="…" />}

      {/* Results */}
      {fg && explorer.graph && (
        explorer.graph.nodes.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('explorer.empty')}</p>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <p className="text-sm text-muted-foreground">
                {t('explorer.counts', {
                  n: explorer.graph.nodes.length,
                  edges: explorer.graph.edges.length,
                })}
              </p>
              <Button
                type="button"
                variant="secondary"
                onClick={() =>
                  downloadText(
                    'chorus-explorer.json',
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
                    'chorus-explorer.graphml',
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
                    'chorus-explorer.html',
                    toGraphHtml({
                      title: `${t('explorer.title')} — ${seedType === 'entity' ? entity : author}`,
                      nodes: fg.nodes,
                      edges: fg.edges,
                      positions: apiRef.current?.getPositions() ?? {},
                      nodeStyles: EXPLORER_NODE_STYLES,
                      edgeStyles: EXPLORER_EDGE_STYLES,
                      legend: [
                        { kind: 'seed', label: t('explorer.legend_seed') },
                        { kind: 'author', label: t('explorer.legend_author') },
                        { kind: 'topic', label: t('explorer.legend_topic') },
                      ],
                    }),
                    'text/html',
                  )
                }
              >
                {t('graph.export_html')}
              </Button>
            </div>
            {seededMutation?.data?.truncated && <Banner variant="info">{t('explorer.capped')}</Banner>}
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
              nodeStyles={EXPLORER_NODE_STYLES}
              edgeStyles={EXPLORER_EDGE_STYLES}
              selectedIds={explorer.selectedIds}
              onSelectionChange={explorer.select}
              expandActions={expandActions}
              onExpandAction={onExpandAction}
              onDeleteNodes={explorer.removeNodes}
              expandingId={explorer.expandingId}
              statusText={t('graph.hint')}
              legend={[
                { kind: 'seed', label: t('explorer.legend_seed') },
                { kind: 'author', label: t('explorer.legend_author') },
                { kind: 'topic', label: t('explorer.legend_topic') },
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
