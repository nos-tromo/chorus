/**
 * Selection → expand-action computation and dispatch, shared by
 * `ToolExplorer` (a form-driven canvas) and `AgentGraphCard` (an inline
 * trace-entry card) — both ride the same `useUnifiedExplorer` graph and
 * need the same per-node-kind action set: an `author` node offers "expand
 * topics" and "expand ties"; a `topic` node offers just "expand mentions".
 *
 * Pure — no React, no hook state. `computeExpandActions` takes the single
 * selected node (or null) and a label lookup; `dispatchExpandAction` takes
 * the three expand functions `useUnifiedExplorer` returns and routes an
 * action id + node id to the right one.
 */
import type { Strings } from '../i18n'
import type { ExplorerNode } from './explorerElements'

export type ExplorerActionId = 'topics' | 'ties' | 'mentions'

export interface ExplorerExpandAction {
  id: ExplorerActionId
  label: string
}

export type LabelLookup = (key: keyof Strings) => string

/**
 * Author nodes get two actions (topics, ties); topic nodes get one
 * (mentions). No selection (or a multi-selection resolved to null by the
 * caller) yields an empty action list.
 */
export function computeExpandActions(
  node: ExplorerNode | null,
  t: LabelLookup,
): ExplorerExpandAction[] {
  if (!node) return []
  if (node.kind === 'author') {
    return [
      { id: 'topics', label: t('explorer.expand_topics') },
      { id: 'ties', label: t('explorer.expand_ties') },
    ]
  }
  return [{ id: 'mentions', label: t('explorer.expand_mentions') }]
}

export interface ExplorerExpandFns {
  expandTopics: (nodeId: string) => void
  expandTies: (nodeId: string) => void
  expandTopic: (nodeId: string) => void
}

/**
 * Routes an `expandActions` chip click (from `@infra/ui`'s `ForceGraph`)
 * to the matching `useUnifiedExplorer` expand function.
 */
export function dispatchExpandAction(
  fns: ExplorerExpandFns,
  actionId: string,
  nodeId: string,
): void {
  if (actionId === 'ties') fns.expandTies(nodeId)
  else if (actionId === 'topics') fns.expandTopics(nodeId)
  else fns.expandTopic(nodeId)
}
