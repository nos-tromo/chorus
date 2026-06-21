import { describe, it, expect } from 'vitest'
import { POSTS_MENTIONING, AUTHORS_MENTIONING, TOPIC_COOCCURRENCE } from './specs'

describe('ToolSpec objects', () => {
  it('POSTS_MENTIONING has the right name and resultKey', () => {
    expect(POSTS_MENTIONING.name).toBe('posts_mentioning')
    expect(POSTS_MENTIONING.resultKey).toBe('hits')
    expect(POSTS_MENTIONING.titleKey).toBe('posts.title')
    expect(POSTS_MENTIONING.emptyKey).toBe('posts.no_hits')
  })

  it('POSTS_MENTIONING has entity + limit + timeRange fields', () => {
    const kinds = POSTS_MENTIONING.fields.map((f) => f.kind)
    expect(kinds).toContain('entity')
    expect(kinds).toContain('limit')
    expect(kinds).toContain('timeRange')
  })

  it('POSTS_MENTIONING entity field is required', () => {
    const entityField = POSTS_MENTIONING.fields.find((f) => f.kind === 'entity')
    expect(entityField?.required).toBe(true)
  })

  it('AUTHORS_MENTIONING has the right name and resultKey', () => {
    expect(AUTHORS_MENTIONING.name).toBe('authors_mentioning')
    expect(AUTHORS_MENTIONING.resultKey).toBe('authors')
    expect(AUTHORS_MENTIONING.titleKey).toBe('authors_mentioning.title')
    expect(AUTHORS_MENTIONING.emptyKey).toBe('authors_mentioning.none')
  })

  it('TOPIC_COOCCURRENCE has the right name, resultKey, and a text field', () => {
    expect(TOPIC_COOCCURRENCE.name).toBe('topic_co_occurrence')
    expect(TOPIC_COOCCURRENCE.resultKey).toBe('cooccurring')
    expect(TOPIC_COOCCURRENCE.titleKey).toBe('topic_cooc.title')
    expect(TOPIC_COOCCURRENCE.emptyKey).toBe('topic_cooc.none')

    const textField = TOPIC_COOCCURRENCE.fields.find((f) => f.kind === 'text')
    expect(textField).toBeDefined()
    expect(textField?.required).toBe(true)
  })
})
