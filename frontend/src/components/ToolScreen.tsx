import { useState, type FormEvent } from 'react'
import { Banner, PageHeader, Spinner } from '@infra/ui'
import { useT } from '../config/ConfigContext'
import { describeError } from '../api/errorMessage'
import { useToolCall } from '../hooks/useToolCall'
import { EntityInput } from './form/EntityInput'
import { LimitField } from './form/LimitField'
import { TimeRangeInputs, type TimeRangeValue } from './form/TimeRangeInputs'
import { SubmitButton } from './form/SubmitButton'
import { DataTable } from './DataTable'
import type { ToolSpec, FieldSpec } from '../tools/specs'

// ── Internal state helpers ────────────────────────────────────────────────────

/** Derive the initial form state from the field spec list. */
function buildInitial(fields: FieldSpec[]): Record<string, unknown> {
  const state: Record<string, unknown> = {}
  for (const field of fields) {
    if (field.kind === 'entity') {
      state['_entity'] = ''
    } else if (field.kind === 'text') {
      state[field.payloadKey] = ''
    } else if (field.kind === 'limit') {
      state['limit'] = field.default
    } else if (field.kind === 'timeRange') {
      state['_timeRange'] = { from: undefined, to: undefined }
    }
  }
  return state
}

/** Build the payload object to POST, omitting empty from/to. */
function buildPayload(
  fields: FieldSpec[],
  formState: Record<string, unknown>,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  for (const field of fields) {
    if (field.kind === 'entity') {
      payload['entity'] = formState['_entity']
    } else if (field.kind === 'text') {
      payload[field.payloadKey] = formState[field.payloadKey]
    } else if (field.kind === 'limit') {
      payload['limit'] = formState['limit']
    } else if (field.kind === 'timeRange') {
      const tr = formState['_timeRange'] as TimeRangeValue
      if (tr.from) payload['from'] = tr.from
      if (tr.to) payload['to'] = tr.to
    }
  }
  return payload
}

/** True when all required fields are non-empty. */
function isComplete(fields: FieldSpec[], formState: Record<string, unknown>): boolean {
  for (const field of fields) {
    if (field.kind === 'entity' && !formState['_entity']) return false
    if (field.kind === 'text' && !formState[field.payloadKey]) return false
  }
  return true
}

// ── ToolScreen ────────────────────────────────────────────────────────────────

interface ToolScreenProps {
  spec: ToolSpec
}

export function ToolScreen({ spec }: ToolScreenProps) {
  const t = useT()
  const mutation = useToolCall<Record<string, unknown[]>>(spec.name)

  const [formState, setFormState] = useState<Record<string, unknown>>(
    () => buildInitial(spec.fields),
  )

  function setField(key: string, value: unknown) {
    setFormState((prev) => ({ ...prev, [key]: value }))
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    mutation.mutate(buildPayload(spec.fields, formState))
  }

  const ready = isComplete(spec.fields, formState)
  const rows = mutation.data ? (mutation.data[spec.resultKey] as Record<string, unknown>[] | undefined) ?? [] : null
  const errorDescriptor = mutation.error ? describeError(mutation.error) : null

  return (
    <div className="p-8 space-y-6">
      <PageHeader
        title={t(spec.titleKey)}
        caption={spec.captionKey ? t(spec.captionKey) : undefined}
      />

      {/* Form */}
      <form onSubmit={handleSubmit} className="space-y-4">
        {spec.fields.map((field, idx) => {
          if (field.kind === 'entity') {
            const label = field.labelKey ? t(field.labelKey) : t('common.entity_input')
            return (
              <EntityInput
                key={idx}
                label={label}
                value={(formState['_entity'] as string) ?? ''}
                onChange={(v) => setField('_entity', v)}
                required={field.required}
              />
            )
          }

          if (field.kind === 'text') {
            const label = t(field.labelKey)
            return (
              <EntityInput
                key={idx}
                label={label}
                value={(formState[field.payloadKey] as string) ?? ''}
                onChange={(v) => setField(field.payloadKey, v)}
                required={field.required}
              />
            )
          }

          if (field.kind === 'limit') {
            return (
              <LimitField
                key={idx}
                label={t('common.limit')}
                min={field.min}
                max={field.max}
                value={(formState['limit'] as number) ?? field.default}
                onChange={(v) => setField('limit', v)}
              />
            )
          }

          if (field.kind === 'timeRange') {
            return (
              <TimeRangeInputs
                key={idx}
                fromLabel={t('common.from_ts')}
                toLabel={t('common.to_ts')}
                value={(formState['_timeRange'] as TimeRangeValue) ?? {}}
                onChange={(v) => setField('_timeRange', v)}
              />
            )
          }

          return null
        })}

        <SubmitButton loading={mutation.isPending} disabled={!ready}>
          {t('common.search')}
        </SubmitButton>
      </form>

      {/* Error */}
      {errorDescriptor && (
        <Banner variant="danger">{t(errorDescriptor.key, errorDescriptor.vars)}</Banner>
      )}

      {/* Loading spinner */}
      {mutation.isPending && <Spinner label="…" />}

      {/* Results */}
      {rows !== null && !mutation.isPending && (
        <DataTable
          rows={rows}
          columns={spec.columns}
          empty={t(spec.emptyKey)}
        />
      )}
    </div>
  )
}
