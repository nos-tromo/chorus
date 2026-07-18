/**
 * Pure merge for the growing explorer graph. Nodes dedup by id with the
 * existing node winning (it carries authoritative ring/seed flags); edges
 * dedup by a caller-supplied key so parallel edges of different kinds survive.
 */
export interface GraphState<N extends { id: string }, E> {
  nodes: N[]
  edges: E[]
}

export function mergeGraph<N extends { id: string }, E>(
  current: GraphState<N, E>,
  added: { nodes: N[]; edges: E[] },
  edgeKey: (e: E) => string
): GraphState<N, E> {
  const seenNodes = new Set(current.nodes.map((n) => n.id))
  const nodes = [...current.nodes]
  for (const n of added.nodes) {
    if (seenNodes.has(n.id)) continue
    seenNodes.add(n.id)
    nodes.push(n)
  }
  const seenEdges = new Set(current.edges.map(edgeKey))
  const edges = [...current.edges]
  for (const e of added.edges) {
    const k = edgeKey(e)
    if (seenEdges.has(k)) continue
    seenEdges.add(k)
    edges.push(e)
  }
  return { nodes, edges }
}
