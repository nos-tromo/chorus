/**
 * ToolSocial — `social_network_around` tool screen.
 *
 * Ego network of follows/friends around an author. Renders a reactive,
 * incrementally-expandable ForceGraph (seed centred by ring). Form mirrors
 * 08_social_network_around.py.
 */

import { useMemo, useRef, useState, type FormEvent } from 'react'
import { Banner, Button, ForceGraph, Spinner, type ForceGraphHandle } from '@infra/ui'
import { useT } from '../config/ConfigContext'
import { useToolCall } from '../hooks/useToolCall'
import { useSocialExplorer } from '../hooks/useGraphExplorer'
import { EntityInput } from '../components/form/EntityInput'
import { LimitField } from '../components/form/LimitField'
import { SubmitButton } from '../components/form/SubmitButton'
import { SOCIAL_NODE_STYLES, SOCIAL_EDGE_STYLES, toSocialForceGraph } from '../lib/socialElements'
import { downloadText, toGraphHtml, toGraphJson, toGraphML } from '../lib/graphExport'
import type { SocialNetworkAroundOut } from '../api/types'

const EXPAND_LIMIT = 50

export function ToolSocial() {
  const t = useT()
  const mutation = useToolCall<SocialNetworkAroundOut>('social_network_around')
  const explorer = useSocialExplorer()
  const apiRef = useRef<ForceGraphHandle | null>(null)

  const [author, setAuthor] = useState('')
  const [depth, setDepth] = useState(2)
  const [limit, setLimit] = useState(25)
  const [secondRingLimit, setSecondRingLimit] = useState(50)

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    mutation.mutate(
      { author, depth, limit, second_ring_limit: secondRingLimit },
      { onSuccess: (out) => explorer.seedFrom(out) },
    )
  }

  const errorMessage =
    mutation.error instanceof Error
      ? mutation.error.message
      : mutation.error
        ? String(mutation.error)
        : ''

  const followsCount = explorer.graph
    ? explorer.graph.edges.filter((e) => e.kind === 'follows').length
    : 0
  const friendsCount = explorer.graph
    ? explorer.graph.edges.filter((e) => e.kind === 'friends').length
    : 0

  const fg = useMemo(
    () => (explorer.graph ? toSocialForceGraph(explorer.graph) : null),
    [explorer.graph],
  )

  return (
    <div className="p-8 space-y-6">
      {/* Title + caption */}
      <div>
        <h1 className="text-2xl font-semibold">{t('social.title')}</h1>
        <p className="text-sm text-muted-foreground mt-1">{t('social.caption')}</p>
      </div>

      {/* Form */}
      <form onSubmit={handleSubmit} className="space-y-4 max-w-lg">
        <EntityInput
          label={t('social.author_input')}
          value={author}
          onChange={setAuthor}
          required
        />
        <LimitField
          label={t('social.depth')}
          min={1}
          max={2}
          value={depth}
          onChange={setDepth}
        />
        <div className="grid grid-cols-2 gap-4">
          <LimitField
            label={t('social.limit')}
            min={1}
            max={200}
            value={limit}
            onChange={setLimit}
          />
          <LimitField
            label={t('social.second_ring_limit')}
            min={1}
            max={500}
            value={secondRingLimit}
            onChange={setSecondRingLimit}
          />
        </div>
        <SubmitButton loading={mutation.isPending} disabled={!author}>
          {t('social.build')}
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
          <p className="text-sm text-muted-foreground">{t('social.empty')}</p>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <p className="text-sm text-muted-foreground">
                {t('social.counts', {
                  n: explorer.graph.nodes.length,
                  edges: explorer.graph.edges.length,
                  follows: followsCount,
                  friends: friendsCount,
                })}
              </p>
              <Button
                type="button"
                variant="secondary"
                onClick={() =>
                  downloadText(
                    'chorus-social.json',
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
                    'chorus-social.graphml',
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
                    'chorus-social.html',
                    toGraphHtml({
                      title: `${t('social.title')} — ${author}`,
                      nodes: fg.nodes,
                      edges: fg.edges,
                      positions: apiRef.current?.getPositions() ?? {},
                      nodeStyles: SOCIAL_NODE_STYLES,
                      edgeStyles: SOCIAL_EDGE_STYLES,
                      legend: [
                        { kind: 'seed', label: t('social.legend_seed') },
                        { kind: 'ring1', label: t('social.legend_ring1') },
                        { kind: 'ring2', label: t('social.legend_ring2') },
                        { kind: 'ringN', label: t('social.legend_ringN') },
                      ],
                    }),
                    'text/html',
                  )
                }
              >
                {t('graph.export_html')}
              </Button>
            </div>
            {mutation.data?.truncated && <Banner variant="info">{t('social.capped')}</Banner>}
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
              nodeStyles={SOCIAL_NODE_STYLES}
              edgeStyles={SOCIAL_EDGE_STYLES}
              selectedId={explorer.selectedId}
              onSelectNode={explorer.select}
              onExpandNode={explorer.expand}
              expandingId={explorer.expandingId}
              statusText={t('graph.hint')}
              legend={[
                { kind: 'seed', label: t('social.legend_seed') },
                { kind: 'ring1', label: t('social.legend_ring1') },
                { kind: 'ring2', label: t('social.legend_ring2') },
                { kind: 'ringN', label: t('social.legend_ringN') },
              ]}
              labels={{
                minEdges: t('graph.min_edges'),
                edgeLength: t('graph.edge_length'),
                zoom: t('graph.zoom'),
                reset: t('graph.reset'),
                fit: t('graph.fit'),
                expandSelected: t('graph.expand_node'),
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
