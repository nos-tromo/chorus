/**
 * Unified explorer state: one growing {nodes, edges} canvas over both graph
 * families (network_around/expand_network_node's author+topic mentions
 * graph, and social_network_around/expand_social_node's author follows/
 * friends graph). Generalizes `useGraphExplorer.ts`'s two hooks into one —
 * same EXPAND_LIMIT, busy-guard, in-flight anchor discard, and removeNodes
 * behavior, but a single `ExplorerNode`/`ExplorerEdge` shape so an author
 * touched by both a topic-mention and a follows edge is one node, not two.
 */
import { useCallback, useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { callTool } from '../api/tools'
import { mergeGraph, type GraphState } from '../lib/graphExplorer'
import { explorerEdgeKey, type ExplorerEdge, type ExplorerNode } from '../lib/explorerElements'
import type {
  ExpandNetworkNodeOut,
  ExpandSocialNodeOut,
  NetworkAroundOut,
  SocialNetworkAroundOut
} from '../api/types'

const EXPAND_LIMIT = 50

interface Added {
  nodes: ExplorerNode[]
  edges: ExplorerEdge[]
  truncated: boolean
}

function errText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

function mapNetworkExpand(out: ExpandNetworkNodeOut): Added {
  return {
    nodes: out.nodes.map((n) => ({
      id: n.id,
      kind: n.kind,
      label: n.label,
      entity_id: n.entity_id,
      is_seed: n.is_seed
    })),
    edges: out.edges.map((e) => ({ source: e.source, target: e.target, kind: 'mentions', weight: e.weight })),
    truncated: out.truncated
  }
}

function mapSocialExpand(out: ExpandSocialNodeOut): Added {
  return {
    nodes: out.nodes.map((n) => ({ id: n.id, kind: 'author', label: n.label, entity_id: null, is_seed: false })),
    edges: out.edges.map((e) => ({ source: e.source, target: e.target, kind: e.kind, directed: e.directed })),
    truncated: out.truncated
  }
}

export function useUnifiedExplorer() {
  const [graph, setGraph] = useState<GraphState<ExplorerNode, ExplorerEdge> | null>(null)
  const [selectedIds, select] = useState<string[]>([])
  // UI disables expansion triggers while expandingId is set (all three expand
  // fns share this flag — a topic expansion and a ties expansion never race).
  const [expandingId, setExpandingId] = useState<string | null>(null)
  const [expansionTruncated, setExpansionTruncated] = useState(false)
  const [expandError, setExpandError] = useState<string | null>(null)
  // Mirrors expandingId for the guard in `runExpansion`, which reads it synchronously
  // from a useCallback closure — state alone can lag a double-click fired before React
  // re-renders.
  const expandingRef = useRef<string | null>(null)

  const networkMutation = useMutation({
    mutationFn: (nodeId: string) =>
      callTool<ExpandNetworkNodeOut>('expand_network_node', { node_id: nodeId, limit: EXPAND_LIMIT })
  })
  const socialMutation = useMutation({
    mutationFn: (nodeId: string) =>
      callTool<ExpandSocialNodeOut>('expand_social_node', {
        author_id: nodeId.replace(/^author:/, ''),
        limit: EXPAND_LIMIT
      })
  })

  const seedFromNetwork = useCallback((out: NetworkAroundOut) => {
    setGraph({
      nodes: out.nodes,
      edges: out.edges.map((e) => ({ source: e.source, target: e.target, kind: 'mentions', weight: e.weight }))
    })
    select([])
    setExpansionTruncated(false)
    setExpandError(null)
  }, [])

  const seedFromSocial = useCallback((out: SocialNetworkAroundOut) => {
    setGraph({
      nodes: out.nodes.map((n) => ({ id: n.id, kind: 'author', label: n.label, entity_id: null, is_seed: n.is_seed })),
      edges: out.edges.map((e) => ({ source: e.source, target: e.target, kind: e.kind, directed: e.directed }))
    })
    select([])
    setExpansionTruncated(false)
    setExpandError(null)
  }, [])

  const runExpansion = useCallback(
    <TRaw,>(
      nodeId: string,
      mutate: (
        variables: string,
        options: {
          onSuccess: (out: TRaw) => void
          onError: (err: unknown) => void
          onSettled: () => void
        }
      ) => void,
      mapResult: (out: TRaw) => Added
    ) => {
      if (expandingRef.current !== null) return
      expandingRef.current = nodeId
      setExpandingId(nodeId)
      setExpandError(null)
      mutate(nodeId, {
        onSuccess: (out) => {
          const added = mapResult(out)
          // If the anchor node was removed while this expansion was in flight, discard
          // the whole result — merging it back in would re-add neighbours orphaned from
          // the graph.
          setGraph((g) => {
            if (!g || !g.nodes.some((n) => n.id === nodeId)) return g
            setExpansionTruncated(added.truncated)
            return mergeGraph(g, added, explorerEdgeKey)
          })
        },
        onError: (err) => setExpandError(errText(err)),
        onSettled: () => {
          expandingRef.current = null
          setExpandingId(null)
        }
      })
    },
    []
  )

  const expandTopics = useCallback(
    (authorNodeId: string) => runExpansion(authorNodeId, networkMutation.mutate, mapNetworkExpand),
    [runExpansion, networkMutation.mutate]
  )

  const expandTopic = useCallback(
    (topicNodeId: string) => runExpansion(topicNodeId, networkMutation.mutate, mapNetworkExpand),
    [runExpansion, networkMutation.mutate]
  )

  const expandTies = useCallback(
    (authorNodeId: string) => runExpansion(authorNodeId, socialMutation.mutate, mapSocialExpand),
    [runExpansion, socialMutation.mutate]
  )

  // View-state only: declutters the canvas, never touches graph data.
  const removeNodes = useCallback((nodeIds: string[]) => {
    const removed = new Set(nodeIds)
    setGraph((g) => {
      if (!g) return g
      return {
        nodes: g.nodes.filter((n) => !removed.has(n.id)),
        edges: g.edges.filter((e) => !removed.has(e.source) && !removed.has(e.target))
      }
    })
    select((current) => current.filter((id) => !removed.has(id)))
  }, [])

  return {
    graph,
    seedFromNetwork,
    seedFromSocial,
    expandTopics,
    expandTies,
    expandTopic,
    selectedIds,
    select,
    removeNodes,
    expandingId,
    expansionTruncated,
    expandError
  }
}
