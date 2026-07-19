/**
 * AgentGraphCard — inline, expandable graph rendered under an agent trace
 * entry that carries a graph-tool result (network_around, expand_network_node,
 * social_network_around, expand_social_node). Mirrors the unified-explorer
 * wiring in ToolExplorer.tsx, but seeds from the trace entry's `result`
 * instead of a form submission, and renders nothing for any other tool or a
 * null/malformed result.
 */

import { useEffect, useMemo } from 'react'
import { Banner, ForceGraph } from '@infra/ui'
import { useT } from '../config/ConfigContext'
import { useUnifiedExplorer } from '../hooks/useUnifiedExplorer'
import { EXPLORER_NODE_STYLES, EXPLORER_EDGE_STYLES, toExplorerForceGraph } from '../lib/explorerElements'
import { computeExpandActions, dispatchExpandAction } from '../lib/explorerActions'
import type {
  AgentTraceEntry,
  ExpandNetworkNodeOut,
  ExpandSocialNodeOut,
  NetworkAroundOut,
  NetworkNode,
  SocialNetworkAroundOut,
  SocialNode,
} from '../api/types'

const EXPAND_LIMIT = 50

const NETWORK_TOOLS = new Set(['network_around', 'expand_network_node'])
const SOCIAL_TOOLS = new Set(['social_network_around', 'expand_social_node'])

// Union of graph-shaped tools this card knows how to render. Exported so callers
// (e.g. Agent.tsx) can filter trace entries down to graph tools before mounting
// a card, instead of mounting one per non-null result and relying on this
// component's own no-op return for everything else.
export const GRAPH_TRACE_TOOLS = new Set([...NETWORK_TOOLS, ...SOCIAL_TOOLS])

interface AgentGraphCardProps {
  entry: AgentTraceEntry
}

export function AgentGraphCard({ entry }: AgentGraphCardProps) {
  const t = useT()
  const isNetwork = NETWORK_TOOLS.has(entry.tool)
  const isSocial = SOCIAL_TOOLS.has(entry.tool)

  const explorer = useUnifiedExplorer()

  useEffect(() => {
    if (!entry.result) return
    if (
      !Array.isArray((entry.result as { nodes?: unknown }).nodes) ||
      !Array.isArray((entry.result as { edges?: unknown }).edges)
    ) {
      return
    }

    if (isNetwork) {
      let out: NetworkAroundOut
      if (entry.tool === 'expand_network_node') {
        const expandResult = entry.result as unknown as ExpandNetworkNodeOut
        const nodeId = entry.arguments.node_id
        const nodes: NetworkNode[] = [...expandResult.nodes]
        let seedNodeId: string | null = null
        if (typeof nodeId === 'string') {
          const anchor: NetworkNode = {
            id: nodeId,
            kind: nodeId.startsWith('topic:') ? 'topic' : 'author',
            label: nodeId.split(':').slice(1).join(':'),
            entity_id: null,
            is_seed: true,
          }
          nodes.unshift(anchor)
          seedNodeId = nodeId
        }
        out = {
          seed: '',
          seed_node_id: seedNodeId,
          nodes,
          edges: expandResult.edges,
          truncated: expandResult.truncated,
        }
      } else {
        out = entry.result as unknown as NetworkAroundOut
      }
      explorer.seedFromNetwork(out)
    } else if (isSocial) {
      let out: SocialNetworkAroundOut
      if (entry.tool === 'expand_social_node') {
        const expandResult = entry.result as unknown as ExpandSocialNodeOut
        const authorId = entry.arguments.author_id
        const neighbours: SocialNode[] = expandResult.nodes.map((n) => ({
          id: n.id,
          label: n.label,
          ring: 1,
          is_seed: false,
        }))
        let seedNodeId: string | null = null
        if (typeof authorId === 'string') {
          const anchor: SocialNode = {
            id: `author:${authorId}`,
            label: authorId,
            ring: 0,
            is_seed: true,
          }
          neighbours.unshift(anchor)
          seedNodeId = anchor.id
        }
        out = {
          seed: '',
          seed_node_id: seedNodeId,
          nodes: neighbours,
          edges: expandResult.edges,
          truncated: expandResult.truncated,
        }
      } else {
        out = entry.result as unknown as SocialNetworkAroundOut
      }
      explorer.seedFromSocial(out)
    }
    // Seed only when the trace entry identity changes — seedFromNetwork/
    // seedFromSocial identities are stable across renders (useCallback with
    // no deps).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entry])

  const fg = useMemo(
    () => (explorer.graph ? toExplorerForceGraph(explorer.graph) : null),
    [explorer.graph],
  )

  const hasGraphShape =
    !!entry.result &&
    Array.isArray((entry.result as { nodes?: unknown }).nodes) &&
    Array.isArray((entry.result as { edges?: unknown }).edges)
  if (!hasGraphShape || !GRAPH_TRACE_TOOLS.has(entry.tool)) return null
  if (!fg) return null

  const selectedNode =
    explorer.selectedIds.length === 1
      ? (explorer.graph?.nodes.find((n) => n.id === explorer.selectedIds[0]) ?? null)
      : null
  const expandActions = computeExpandActions(selectedNode, t)
  const onExpandAction = (actionId: string, nodeId: string) =>
    dispatchExpandAction(explorer, actionId, nodeId)

  return (
    <div className="mt-3 rounded-lg border border-border p-3 space-y-2">
      <p className="text-xs font-medium text-muted-foreground">
        {t('agent.graph_result', { tool: entry.tool })}
      </p>

      {explorer.expansionTruncated && (
        <Banner variant="info">{t('graph.expansion_capped', { limit: EXPAND_LIMIT })}</Banner>
      )}
      {explorer.expandError && (
        <Banner variant="danger">
          {t('graph.expand_failed', { error: explorer.expandError })}
        </Banner>
      )}

      <ForceGraph
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
}
