/**
 * Explorer state for the two graph screens and the agent's inline graphs:
 * a growing {nodes, edges} graph seeded from a seed-tool payload and grown by
 * the expand-on-click tools, plus selection. Merge/ring assignment is view
 * state — the backend returns flat neighbour lists.
 */
import { useCallback, useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { callTool } from '../api/tools'
import { mergeGraph, type GraphState } from '../lib/graphExplorer'
import type {
  ExpandNetworkNodeOut,
  ExpandSocialNodeOut,
  NetworkAroundOut,
  NetworkEdge,
  NetworkNode,
  SocialEdge,
  SocialNetworkAroundOut,
  SocialNode
} from '../api/types'

const EXPAND_LIMIT = 50

function errText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

export function useNetworkExplorer() {
  const [graph, setGraph] = useState<GraphState<NetworkNode, NetworkEdge> | null>(null)
  const [selectedIds, select] = useState<string[]>([])
  // UI disables expansion triggers while expandingId is set (concurrent expansions share this flag).
  const [expandingId, setExpandingId] = useState<string | null>(null)
  const [expansionTruncated, setExpansionTruncated] = useState(false)
  // Mirrors expandingId for the guard in `expand`, which reads it synchronously from a
  // useCallback closure — state alone can lag a double-click fired before React re-renders.
  const expandingRef = useRef<string | null>(null)

  const mutation = useMutation({
    mutationFn: (nodeId: string) =>
      callTool<ExpandNetworkNodeOut>('expand_network_node', { node_id: nodeId, limit: EXPAND_LIMIT })
  })

  const seedFrom = useCallback((out: NetworkAroundOut) => {
    setGraph({ nodes: out.nodes, edges: out.edges })
    select([])
    setExpansionTruncated(false)
  }, [])

  const expand = useCallback(
    (nodeId: string) => {
      if (expandingRef.current !== null) return
      expandingRef.current = nodeId
      setExpandingId(nodeId)
      mutation.mutate(nodeId, {
        onSuccess: (out) => {
          // If the anchor node was removed while this expansion was in flight, discard the
          // whole result — merging it back in would re-add neighbours orphaned from the graph.
          setGraph((g) => {
            if (!g || !g.nodes.some((n) => n.id === nodeId)) return g
            setExpansionTruncated(out.truncated)
            return mergeGraph(g, out, (e) => `${e.source}__${e.target}`)
          })
        },
        onSettled: () => {
          expandingRef.current = null
          setExpandingId(null)
        }
      })
    },
    [mutation]
  )

  // View-state only: declutters the canvas, never touches graph data. Removed
  // nodes return if re-added via a neighbour's expansion (ringsRef-equivalent
  // state, if any, is left untouched — harmless, and re-adding the same id is
  // the desirable outcome).
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
    seedFrom,
    expand,
    removeNodes,
    expandingId,
    expansionTruncated,
    selectedIds,
    select,
    expandError: mutation.isError ? errText(mutation.error) : null
  }
}

export function useSocialExplorer() {
  const [graph, setGraph] = useState<GraphState<SocialNode, SocialEdge> | null>(null)
  const [selectedIds, select] = useState<string[]>([])
  // UI disables expansion triggers while expandingId is set (concurrent expansions share this flag).
  const [expandingId, setExpandingId] = useState<string | null>(null)
  const [expansionTruncated, setExpansionTruncated] = useState(false)
  // Mirrors expandingId for the guard in `expand`, which reads it synchronously from a
  // useCallback closure — state alone can lag a double-click fired before React re-renders.
  const expandingRef = useRef<string | null>(null)
  // Ring lookup for ring+1 assignment on expansion; refreshed on every graph set.
  const ringsRef = useRef<Map<string, number>>(new Map())

  const remember = (nodes: SocialNode[]) => {
    // First-wins: never overwrite an existing entry (mirrors mergeGraph's dedup logic).
    for (const n of nodes) {
      if (!ringsRef.current.has(n.id)) ringsRef.current.set(n.id, n.ring)
    }
  }

  const mutation = useMutation({
    mutationFn: (nodeId: string) =>
      callTool<ExpandSocialNodeOut>('expand_social_node', {
        author_id: nodeId.replace(/^author:/, ''),
        limit: EXPAND_LIMIT
      })
  })

  const seedFrom = useCallback((out: SocialNetworkAroundOut) => {
    ringsRef.current = new Map()
    remember(out.nodes)
    setGraph({ nodes: out.nodes, edges: out.edges })
    select([])
    setExpansionTruncated(false)
  }, [])

  const expand = useCallback(
    (nodeId: string) => {
      if (expandingRef.current !== null) return
      expandingRef.current = nodeId
      setExpandingId(nodeId)
      mutation.mutate(nodeId, {
        onSuccess: (out) => {
          const ring = (ringsRef.current.get(nodeId) ?? 0) + 1
          const added = {
            nodes: out.nodes.map((n) => ({ id: n.id, label: n.label, ring, is_seed: false })),
            edges: out.edges
          }
          // If the anchor node (namespaced id, e.g. "author:auth-b") was removed while this
          // expansion was in flight, discard the whole result — merging it back in would
          // re-add neighbours orphaned from the graph.
          setGraph((g) => {
            if (!g || !g.nodes.some((n) => n.id === nodeId)) return g
            remember(added.nodes)
            setExpansionTruncated(out.truncated)
            return mergeGraph(g, added, (e) => `${e.source}__${e.target}__${e.kind}`)
          })
        },
        onSettled: () => {
          expandingRef.current = null
          setExpandingId(null)
        }
      })
    },
    [mutation]
  )

  // View-state only: declutters the canvas, never touches graph data. Ring
  // bookkeeping (ringsRef) is left untouched on removal — harmless, and a
  // re-added node (via a neighbour's expansion) regains its old ring, which
  // is the desirable outcome.
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
    seedFrom,
    expand,
    removeNodes,
    expandingId,
    expansionTruncated,
    selectedIds,
    select,
    expandError: mutation.isError ? errText(mutation.error) : null
  }
}
