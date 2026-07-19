import { describe, it, expect, vi } from 'vitest'
import { computeExpandActions, dispatchExpandAction } from './explorerActions'
import type { ExplorerNode } from './explorerElements'

const t = (key: string) => `label:${key}`

const AUTHOR_NODE: ExplorerNode = {
  id: 'author:u1',
  kind: 'author',
  label: 'Aria',
  entity_id: null,
  is_seed: false,
}

const TOPIC_NODE: ExplorerNode = {
  id: 'topic:t1',
  kind: 'topic',
  label: 'Widgets',
  entity_id: 't1',
  is_seed: false,
}

describe('computeExpandActions', () => {
  it('returns no actions for a null selection', () => {
    expect(computeExpandActions(null, t)).toEqual([])
  })

  it('returns topics + ties for an author node', () => {
    expect(computeExpandActions(AUTHOR_NODE, t)).toEqual([
      { id: 'topics', label: 'label:explorer.expand_topics' },
      { id: 'ties', label: 'label:explorer.expand_ties' },
    ])
  })

  it('returns a single mentions action for a topic node', () => {
    expect(computeExpandActions(TOPIC_NODE, t)).toEqual([
      { id: 'mentions', label: 'label:explorer.expand_mentions' },
    ])
  })
})

describe('dispatchExpandAction', () => {
  it('routes "ties" to expandTies', () => {
    const fns = { expandTopics: vi.fn(), expandTies: vi.fn(), expandTopic: vi.fn() }
    dispatchExpandAction(fns, 'ties', 'author:u1')
    expect(fns.expandTies).toHaveBeenCalledWith('author:u1')
    expect(fns.expandTopics).not.toHaveBeenCalled()
    expect(fns.expandTopic).not.toHaveBeenCalled()
  })

  it('routes "topics" to expandTopics', () => {
    const fns = { expandTopics: vi.fn(), expandTies: vi.fn(), expandTopic: vi.fn() }
    dispatchExpandAction(fns, 'topics', 'author:u1')
    expect(fns.expandTopics).toHaveBeenCalledWith('author:u1')
  })

  it('routes any other action id (e.g. "mentions") to expandTopic', () => {
    const fns = { expandTopics: vi.fn(), expandTies: vi.fn(), expandTopic: vi.fn() }
    dispatchExpandAction(fns, 'mentions', 'topic:t1')
    expect(fns.expandTopic).toHaveBeenCalledWith('topic:t1')
  })
})
