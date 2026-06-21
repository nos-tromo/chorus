import type { ColumnDef } from '../components/DataTable'
import type { Strings } from '../i18n'

// ── FieldSpec (discriminated union) ──────────────────────────────────────────

/**
 * A required string field that maps to the `entity` parameter.
 * Uses the common.entity_input label by default, but can be overridden.
 */
export interface EntityFieldSpec {
  kind: 'entity'
  required: true
  /** i18n key for the label; defaults to 'common.entity_input' */
  labelKey?: keyof Strings
}

/**
 * A required free-text string field (e.g. seed topic).
 * Unlike 'entity', the label and payloadKey are configurable.
 */
export interface TextFieldSpec {
  kind: 'text'
  required: true
  labelKey: keyof Strings
  /** Payload key used when building the POST body */
  payloadKey: string
}

/** A numeric limit field. */
export interface LimitFieldSpec {
  kind: 'limit'
  min: number
  max: number
  default: number
}

/** Optional from/to ISO timestamp pair. */
export interface TimeRangeFieldSpec {
  kind: 'timeRange'
}

export type FieldSpec =
  | EntityFieldSpec
  | TextFieldSpec
  | LimitFieldSpec
  | TimeRangeFieldSpec

// ── ToolSpec ─────────────────────────────────────────────────────────────────

export interface ToolSpec {
  /** Backend tool name, used as the POST path segment: /tools/<name> */
  name: string
  /** i18n key for the page title */
  titleKey: keyof Strings
  /** Optional i18n key for the caption below the title */
  captionKey?: keyof Strings
  /** Field declarations; rendered in order */
  fields: FieldSpec[]
  /** Key on the response object that holds the result array */
  resultKey: string
  /** Optional explicit column definitions for DataTable */
  columns?: ColumnDef<Record<string, unknown>>[]
  /** i18n key for the DataTable empty-state message */
  emptyKey: keyof Strings
}

// ── Concrete specs ────────────────────────────────────────────────────────────

export const POSTS_MENTIONING: ToolSpec = {
  name: 'posts_mentioning',
  titleKey: 'posts.title',
  fields: [
    { kind: 'entity', required: true },
    { kind: 'limit', min: 1, max: 200, default: 50 },
    { kind: 'timeRange' },
  ],
  resultKey: 'hits',
  emptyKey: 'posts.no_hits',
}

export const AUTHORS_MENTIONING: ToolSpec = {
  name: 'authors_mentioning',
  titleKey: 'authors_mentioning.title',
  captionKey: 'authors_mentioning.caption',
  fields: [
    { kind: 'entity', required: true },
    { kind: 'limit', min: 1, max: 200, default: 50 },
    { kind: 'timeRange' },
  ],
  resultKey: 'authors',
  emptyKey: 'authors_mentioning.none',
}

export const TOPIC_COOCCURRENCE: ToolSpec = {
  name: 'topic_co_occurrence',
  titleKey: 'topic_cooc.title',
  fields: [
    {
      kind: 'text',
      required: true,
      labelKey: 'topic_cooc.seed_input',
      payloadKey: 'topic',
    },
    { kind: 'limit', min: 1, max: 200, default: 50 },
    { kind: 'timeRange' },
  ],
  resultKey: 'cooccurring',
  emptyKey: 'topic_cooc.none',
}
