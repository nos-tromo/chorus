import { useState, useRef } from 'react'
import { Banner, Button, Card, Input, Spinner } from '@infra/ui'
import { DataTable } from '../components/DataTable'
import { SubmitButton } from '../components/form/SubmitButton'
import { useConfig, useT } from '../config/ConfigContext'
import { useMigrations } from '../hooks/useMigrations'
import { useApplyMigrations, useStartIngest, useStartResolve } from '../hooks/useIngest'
import { useJob, isTerminal } from '../hooks/useJob'
import { ApiError } from '../api/client'

// ── helpers ───────────────────────────────────────────────────────────────────

/** Coerce a Record<string, unknown> into DataTable rows keyed {key, value}. */
function dictToRows(obj: Record<string, unknown>): Array<{ key: string; value: string }> {
  return Object.entries(obj).map(([key, value]) => ({
    key,
    value: typeof value === 'object' ? JSON.stringify(value) : String(value ?? ''),
  }))
}

/** Coerce an unknown job result sub-value into a string for display. */
function safeStr(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

// ── Migrations section ────────────────────────────────────────────────────────

function MigrationsSection({ busy }: { busy: boolean }) {
  const t = useT()
  const migrations = useMigrations()
  const applyMut = useApplyMigrations()
  const [busyBanner, setBusyBanner] = useState(false)
  const [appliedVersions, setAppliedVersions] = useState<string[] | null>(null)

  async function handleApply() {
    setBusyBanner(false)
    setAppliedVersions(null)
    try {
      const result = await applyMut.mutateAsync()
      setAppliedVersions(result.applied)
      void migrations.refetch()
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setBusyBanner(true)
      }
    }
  }

  return (
    <Card className="p-4 space-y-3">
      <h2 className="text-base font-medium">{t('ingest.migrations.header')}</h2>

      {migrations.isLoading && <Spinner label="…" />}

      {migrations.isError && (
        <Banner variant="danger">
          {t('common.tool_call_failed', {
            error:
              migrations.error instanceof Error ? migrations.error.message : String(migrations.error),
          })}
        </Banner>
      )}

      {busyBanner && (
        <Banner variant="info" role="alert">
          {t('common.tool_call_failed', { error: '409 — another job is running' })}
        </Banner>
      )}

      {appliedVersions !== null && (
        <Banner variant="info">
          {t('ingest.migrations.applied', { versions: appliedVersions.join(', ') })}
        </Banner>
      )}

      {migrations.isSuccess && (
        <div className="space-y-2">
          {migrations.data.applied.length > 0 && (
            <p className="text-sm text-muted-foreground">
              Applied: {migrations.data.applied.join(', ')}
            </p>
          )}

          {migrations.data.pending.length > 0 ? (
            <div className="space-y-2">
              <Banner variant="info">
                {t('ingest.migrations.pending', {
                  versions: migrations.data.pending.join(', '),
                })}
              </Banner>
              <Button
                variant="primary"
                onClick={() => void handleApply()}
                disabled={busy || applyMut.isPending}
              >
                {applyMut.isPending ? (
                  <span className="flex items-center gap-2">
                    <Spinner />
                    {t('ingest.migrations.applying')}
                  </span>
                ) : (
                  t('ingest.migrations.apply')
                )}
              </Button>
            </div>
          ) : (
            <p className="text-sm text-green-600">{t('ingest.migrations.uptodate')}</p>
          )}
        </div>
      )}
    </Card>
  )
}

// ── Ingest result display ─────────────────────────────────────────────────────

function IngestResultView({
  result,
}: {
  result: Record<string, unknown>
}) {
  const t = useT()
  const counts = result.counts
  const dropped = result.dropped
  const filtered = result.filtered
  const skipped = result.skipped
  const resolution = result.resolution
  const resolutionError = result.resolution_error

  return (
    <div className="space-y-3 mt-2">
      {typeof counts === 'object' && counts !== null && (
        <div>
          <p className="text-sm font-medium mb-1">{t('ingest.counts.header')}</p>
          <DataTable
            rows={dictToRows(counts as Record<string, unknown>)}
            columns={[
              { key: 'key', label: 'stage' },
              { key: 'value', label: 'count' },
            ]}
            empty=""
          />
        </div>
      )}

      {typeof dropped === 'object' &&
        dropped !== null &&
        Object.values(dropped as Record<string, unknown>).some(Boolean) && (
          <Banner variant="info">
            {t('ingest.counts.dropped', { detail: JSON.stringify(dropped) })}
          </Banner>
        )}

      {typeof filtered === 'object' &&
        filtered !== null &&
        Object.values(filtered as Record<string, unknown>).some(Boolean) && (
          <Banner variant="info">
            {t('ingest.counts.filtered', { detail: JSON.stringify(filtered) })}
          </Banner>
        )}

      {Array.isArray(skipped) && skipped.length > 0 && (
        <Banner variant="info">
          {t('ingest.counts.skipped', { detail: JSON.stringify(skipped) })}
        </Banner>
      )}

      {typeof resolution === 'object' && resolution !== null && (
        <div>
          <p className="text-sm font-medium mb-1">{t('ingest.resolve.summary')}</p>
          <DataTable
            rows={dictToRows(resolution as Record<string, unknown>)}
            columns={[
              { key: 'key', label: 'metric' },
              { key: 'value', label: 'count' },
            ]}
            empty=""
          />
        </div>
      )}

      {resolutionError !== undefined && (
        <Banner variant="danger">
          {t('ingest.resolve.failed', { error: safeStr(resolutionError) })}
        </Banner>
      )}
    </div>
  )
}

// ── JobProgress: polls and renders a job ─────────────────────────────────────

function JobProgress({
  jobId,
  runningLabel,
  doneLabel,
  failedLabel,
  showResult = false,
}: {
  jobId: string
  runningLabel: string
  doneLabel: string
  failedLabel: string
  showResult?: boolean
}) {
  const job = useJob(jobId)

  if (!job.data) {
    return <Spinner label={runningLabel} />
  }

  const { status, result, error } = job.data

  if (status === 'queued' || status === 'running') {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Spinner />
        {runningLabel}
      </p>
    )
  }

  if (status === 'error') {
    return (
      <Banner variant="danger" role="alert">
        {failedLabel.replace('{error}', error ?? '')}
      </Banner>
    )
  }

  // done
  return (
    <div className="space-y-2">
      <p className="text-sm text-green-600">{doneLabel}</p>
      {showResult && result !== null && typeof result === 'object' && (
        <IngestResultView result={result as Record<string, unknown>} />
      )}
    </div>
  )
}

// ── Main Ingestion component ──────────────────────────────────────────────────

export function Ingestion() {
  const t = useT()
  const { ingestion_enabled } = useConfig()

  const [files, setFiles] = useState<File[]>([])
  const [since, setSince] = useState('')
  const [thenResolve, setThenResolve] = useState(false)
  const [ingestJobId, setIngestJobId] = useState<string | null>(null)
  const [resolveJobId, setResolveJobId] = useState<string | null>(null)
  const [ingestError, setIngestError] = useState<string | null>(null)
  const [resolveError, setResolveError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const startIngest = useStartIngest()
  const startResolve = useStartResolve()

  // Track whether active jobs are terminal
  const ingestJob = useJob(ingestJobId)
  const resolveJob = useJob(resolveJobId)

  const ingestRunning = ingestJobId !== null && !isTerminal(ingestJob.data?.status)
  const resolveRunning = resolveJobId !== null && !isTerminal(resolveJob.data?.status)
  const busy = ingestRunning || resolveRunning

  // ── disabled gate ──────────────────────────────────────────────────────────
  if (!ingestion_enabled) {
    return (
      <div className="p-8 space-y-4">
        <h1 className="text-2xl font-semibold">{t('ingest.title')}</h1>
        <Banner variant="info">{t('ingest.disabled')}</Banner>
      </div>
    )
  }

  // ── handlers ───────────────────────────────────────────────────────────────

  async function handleIngest(e: React.FormEvent) {
    e.preventDefault()
    setIngestError(null)
    setIngestJobId(null)
    try {
      const accepted = await startIngest.mutateAsync({
        files,
        since: since.trim() || undefined,
        thenResolve,
      })
      setIngestJobId(accepted.job_id)
    } catch (err) {
      setIngestError(err instanceof Error ? err.message : String(err))
    }
  }

  async function handleResolve() {
    setResolveError(null)
    setResolveJobId(null)
    try {
      const accepted = await startResolve.mutateAsync()
      setResolveJobId(accepted.job_id)
    } catch (err) {
      setResolveError(err instanceof Error ? err.message : String(err))
    }
  }

  function handleFilesChange(e: React.ChangeEvent<HTMLInputElement>) {
    setFiles(Array.from(e.target.files ?? []))
  }

  // ── render ─────────────────────────────────────────────────────────────────

  return (
    <div className="p-8 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">{t('ingest.title')}</h1>
        <p className="text-sm text-muted-foreground mt-1">{t('ingest.caption')}</p>
      </div>

      {/* ── Migrations ─────────────────────────────────────────────────────── */}
      <MigrationsSection busy={busy} />

      {/* ── Upload & ingest ────────────────────────────────────────────────── */}
      <Card className="p-4 space-y-3">
        <h2 className="text-base font-medium">{t('ingest.upload.header')}</h2>
        <p className="text-sm text-muted-foreground">{t('ingest.upload.help')}</p>

        <form onSubmit={(e) => void handleIngest(e)} className="space-y-3">
          {/* File picker */}
          <div className="space-y-1">
            <label className="block text-sm font-medium" htmlFor="csv-upload">
              {t('ingest.upload.label')}
            </label>
            <input
              id="csv-upload"
              ref={fileInputRef}
              type="file"
              multiple
              accept=".csv"
              onChange={handleFilesChange}
              disabled={busy}
              className="block w-full text-sm file:mr-3 file:rounded file:border-0 file:bg-muted file:px-3 file:py-1 file:text-sm file:font-medium hover:file:bg-muted/80 disabled:opacity-50"
            />
          </div>

          {/* Since input */}
          <div className="space-y-1">
            <label className="block text-sm font-medium" htmlFor="since-input">
              {t('ingest.upload.since')}
            </label>
            <Input
              id="since-input"
              type="text"
              value={since}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setSince(e.target.value)}
              placeholder="2024-01-01T00:00:00Z"
              disabled={busy}
            />
          </div>

          {/* Then-resolve checkbox */}
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={thenResolve}
              onChange={(e) => setThenResolve(e.target.checked)}
              disabled={busy}
              aria-label={t('ingest.upload.then_resolve')}
            />
            {t('ingest.upload.then_resolve')}
          </label>

          <SubmitButton
            loading={startIngest.isPending}
            disabled={busy || files.length === 0}
          >
            {t('ingest.upload.start')}
          </SubmitButton>
        </form>

        {ingestError !== null && (
          <Banner variant="danger" role="alert">
            {t('ingest.error.request', { detail: ingestError })}
          </Banner>
        )}

        {ingestJobId !== null && (
          <JobProgress
            jobId={ingestJobId}
            runningLabel={t('ingest.job.running')}
            doneLabel={t('ingest.job.done')}
            failedLabel={t('ingest.job.failed')}
            showResult
          />
        )}
      </Card>

      {/* ── Entity resolution ──────────────────────────────────────────────── */}
      <Card className="p-4 space-y-3">
        <h2 className="text-base font-medium">{t('ingest.resolve.header')}</h2>

        <Button
          variant="primary"
          onClick={() => void handleResolve()}
          disabled={busy || startResolve.isPending}
        >
          {startResolve.isPending ? (
            <span className="flex items-center gap-2">
              <Spinner />
              {t('ingest.resolve.running')}
            </span>
          ) : (
            t('ingest.resolve.start')
          )}
        </Button>

        {resolveError !== null && (
          <Banner variant="danger" role="alert">
            {t('ingest.error.request', { detail: resolveError })}
          </Banner>
        )}

        {resolveJobId !== null && (
          <JobProgress
            jobId={resolveJobId}
            runningLabel={t('ingest.resolve.running')}
            doneLabel={t('ingest.resolve.done')}
            failedLabel={t('ingest.resolve.failed')}
            showResult
          />
        )}
      </Card>
    </div>
  )
}
