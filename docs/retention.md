# retention

Chorus keeps a post (posting, comment or chat message) until its
`retention_until` has passed, and deletes it then with every copy it keeps —
once an operator has switched the sweeper on. Until then nothing is deleted.
The decisions behind the sweeper are ADR 0019.

## Deadlines

`Post.retention_until` is set at ingestion to `ingested_at +
RETENTION_DAYS_DEFAULT` days. `ingested_at` is a chorus-set timestamp — the
time chorus ingested the row, computed once per run — not read from the
upstream (ADR 0011). It is the same anchor for postings, comments, and
messages. The upstream `Timestamp` (content creation) and `Crawled at`
(upstream crawl time) are stored as optional, informational properties and do
**not** drive retention; a row with either missing is still ingested rather
than dropped. The crawling software owns its own retention timer on its own
store — chorus's clock is independent.

`ingested_at` is written once, from the post's own row, so re-ingesting a row
keeps its original deadline (retention is measured from first ingestion). A
post that a reply referenced before its own row arrived — later in the same
file, or in a later run — is filled when that row arrives, deadline included.
`RETENTION_ENABLED=false` fully bypasses retention: every post is written with
`retention_until = null`, so the sweeper never targets it. A deadline is
stamped once: changing `RETENTION_DAYS_DEFAULT` or `RETENTION_ENABLED` later
leaves the deadlines of posts already ingested as they are.

## The sweeper

| Variable | Default | Meaning |
|---|---|---|
| `RETENTION_SWEEP_ENABLED` | `false`, also in `docker/compose.yaml` | The switch. Off, nothing is deleted: the backend schedules no sweep, and `make retention-sweep` is refused (exit 2, recorded as `denied`). |
| `RETENTION_SWEEP_INTERVAL_S` | `86400` | Seconds between the backend's sweeps while the switch is on; the first runs a minute after startup. `0` schedules none: only `make retention-sweep` deletes. |
| `RETENTION_SWEEP_BATCH_SIZE` | `500` | Posts deleted per transaction. |
| `RETENTION_DAYS_DEFAULT` | `365` | The period. At least 1 while the switch is on: startup refuses a shorter one, under which rows of an ingestion still running would count as expired. |
| `RETENTION_ENABLED` | `true` | Off, new posts get no deadline, and the sweeper leaves raw-store rows and staged uploads alone; deadlines stamped earlier still apply. |

The switch, the interval and the batch size are read at startup; the chain's
`config.audit` event states which mode the backend runs in (`off`,
`scheduled` or `on_demand`) and the period stamped on new posts.

### Before switching it on: the dry run

With the backend up, `make retention-report` shows what a sweep would delete
now and what it would leave, as counts and date ranges:

```text
Retention dry run at 2026-11-04T10:00:00.000Z (nothing is recorded or deleted).
Sweeper: off (RETENTION_SWEEP_ENABLED): nothing is deleted until it is switched on.
Retention: on, 365 days; batches of 500 posts.

A sweep now would delete:
  posts: 1840 (postings 210, comments 1570, messages 60)
    expired: 1810, deadlines 2025-09-30 … 2026-11-03
    with an expired post (comments, replies): 28
    bare parent references: 2
    ingested 2024-09-30 … 2026-02-01 (276 to 765 days ago)
  only they reference: aliases 412, hashtags 0, attachments 35
  their raw-store rows: postings 214, comments 1570, messages 60
  raw-store rows no post holds, received before 2025-11-04: postings 0, comments 9, messages 0; received 2025-01-12 … 2025-03-02
  staged uploads untouched since before 2025-11-04: 0 directories, 0 files

Kept:
  posts: 5120; not yet due 5104 (next deadline 2026-11-08)
    without a deadline: ingested with retention off 16, ingested before ingestion times were recorded 0, bare parent references 0
  raw-store author rows: profiles 640, connections 12400
  authors (kept indefinitely, ADR 0006):
    of the deleted posts: 530
    left without any post: 210 (with social edges only 150, without any post or social edge 60, 41 of them with profile data)
    without any post or social edge already: 3 (3 with profile data)
  entities left without an alias: 97
  groups left without a post: 4
```

The report reads only what it needs to count: it returns no id, name or
text, opens the raw store read-only, writes nothing on the audit trail and
works with the switch off. `docker compose -f docker/compose.yaml exec
backend python -m chorus.retention.cli report --json` prints the same numbers
as JSON.

### What a sweep deletes

A post *expires* when its `retention_until` is at or before the sweep's
cutoff (the time the sweep started). Every sweep deletes, in this order:

1. **Expired posts, with what goes with them.** A posting takes along the
   comments on it, and every post takes along the replies to it and to them
   (comment replies, chat-message replies), whatever their own deadline: a
   comment's raw row repeats its posting's text and a reply's its parent's,
   so a thread goes as one. Then what only those posts referenced: their
   aliases (the surface forms extraction took from their text, with the
   aliases' `:RESOLVED_TO` links), hashtags and attachments, and *bare parent
   references* — post nodes a reply created for a parent whose own row never
   arrived, which hold nothing but a uuid.
2. **Their raw-store rows**, in the same batch: every row any ingestion
   stored for those posts.
3. **Raw-store rows no post holds** — `postings`, `comments` and `messages`
   rows the ingestion skipped (a malformed row, a comment whose posting was
   not in its batch) — once chorus received them longer ago than the period.
4. **Staged uploads left behind** under `CHORUS_HOME/uploads` (an upload
   whose job never ran) once untouched for longer than the period.

Steps 3 and 4 apply the period in force, since those copies carry no
deadline of their own; they are skipped while `RETENTION_ENABLED` is off.

### What a sweep keeps

- **Authors** with every property, the personal profile fields the
  `profiles` stage writes (`bio`, `date_of_birth`, and the rest) included,
  their `:FOLLOWS` / `:FRIENDS_WITH` edges, and the `profiles` and
  `connections` raw rows: author data is retained indefinitely by
  deliberate decision (ADR 0006, [compliance.md](compliance.md)). The dry run
  shows how many authors a sweep would leave without any post, or without
  any post or social edge, and how many of those carry profile data.
- **Entities** (canonical name and embedding), **groups** and **platforms**,
  also when a sweep leaves them without an alias or a post; the dry run
  counts those too.
- **Posts without a deadline**, unless they belong to an expired post's
  thread. Releases before 0.10.0 left a post whose own row arrived after a
  reply had referenced it with an author but no text and no deadline; the
  dry run counts these, and re-ingesting their source rows fills them and
  gives them one.
- Outside chorus's stores: the CSV exports under `INGESTION_SOURCE_DIR` are
  the operator's files, read by `make ingest` and never deleted by chorus;
  remove them once they are ingested. Neo4j backups are data-plane's: a
  deleted post stays in a backup until that backup is deleted under
  data-plane's rotation. The audit trail keeps the deletion records, which
  name the posts by uuid and their authors and entities by id, under
  obs-plane's retention (below).

### How a sweep runs

- **Bounded batches.** Expired posts are taken oldest deadline first, each
  with its thread, until `RETENTION_SWEEP_BATCH_SIZE` posts; a thread is
  never split, so a thread larger than a batch goes alone. Raw-store rows no
  post holds are checked and deleted a batch size at a time, staged uploads
  one at a time.
- **Recorded before it commits.** Each batch is one `delete` /
  `retention.sweep` event, written inside the batch's graph and raw-store
  transactions before either commits; if it cannot be written both roll back.
  The raw store commits first: if the graph's commit then fails, the posts
  stay expired and the next sweep takes them again. A commit that fails after
  its event was written is recorded with `outcome="error"`. A staged upload's
  event is written before its files are deleted, followed by an `error` event
  for any file that could not be deleted. What each event holds:
  [audit.md](audit.md#targets-and-criteria).
- **A failed audit write stops it.** The batch rolls back and the sweep ends;
  the backend shows `chorus_retention_sweep_halted 1` on `/metrics` and logs
  the halt with the counts deleted before it. The next interval tries again,
  and since a batch only commits after its event, nothing is deleted while
  the trail stays unwritable.
- **One at a time.** A lock under `CHORUS_HOME` keeps the scheduled sweep and
  a one-off sweep apart; the second one is skipped (exit 1 for the command).
  At shutdown a running sweep stops after its current batch.

### Running a sweep by hand

`make retention-sweep` runs one sweep now inside the running backend, as the
invoking host user, while the switch is on; it is recorded like the scheduled
one, with its own chain. It follows `AUDIT_REQUIRE_PURPOSE` (the compose
default does not name `delete`); `CASE_REF` / `REASON` state a purpose:

```bash
make retention-sweep REASON="retention period"
```

With `RETENTION_SWEEP_INTERVAL_S=0` this is the only way a sweep runs, for
example from a host timer that calls it at night.

### Monitoring

`GET /metrics` carries, counts only: `chorus_retention_sweep_scheduled` (1
while the backend sweeps on a schedule), `chorus_retention_sweep_halted`,
`chorus_retention_sweep_last_success_timestamp_seconds`,
`chorus_retention_sweep_runs_total{outcome}` (`ok`, `halted`, `error`,
`busy`) and `chorus_retention_deleted_total{kind}` (`posts`, `aliases`,
`hashtags`, `attachments`, `raw_rows`, `upload_files`). The operational log
has one line per sweep with the same counts.

## Audit trail

Chorus no longer keeps audit data of its own (apart from the frozen legacy
file below). Its audit events leave through the backend's stdout as they are
written and are kept by obs-plane's audit store, under that store's retention
and access rules (obs-plane's
[`docs/audit.md`](https://github.com/nos-tromo/obs-plane/blob/main/docs/audit.md));
what chorus records is in [audit.md](audit.md).

## Legacy SQLite audit log

Releases before 0.8.0 wrote audit rows to `$CHORUS_HOME/audit.sqlite` on the
`chorus-state` volume (`/var/lib/chorus/audit.sqlite` in the container, or
wherever `AUDIT_DB_PATH` pointed). Chorus no longer reads or writes that file.
It stays where it is, frozen: its own triggers reject `UPDATE` and `DELETE`
on its rows.

Its rows are kept until the end of the calendar year after the year the last
row was written, and then deleted by the operator. The last row's timestamp:

```bash
sudo sqlite3 -readonly "$(docker volume inspect chorus-state --format '{{ .Mountpoint }}')/audit.sqlite" \
  'SELECT max(ts) FROM audit_log;'
```

To read the rows for a review in the meantime, query the file read-only where
it lies, as JSON (no copy leaves the volume):

```bash
sudo sqlite3 -readonly "$(docker volume inspect chorus-state --format '{{ .Mountpoint }}')/audit.sqlite" \
  '.mode json' 'SELECT * FROM audit_log ORDER BY id;'
```

On a host without the `sqlite3` CLI, the backend's Python reads it the same
way: `docker compose -f docker/compose.yaml exec -T backend python -c "import
json, sqlite3; c = sqlite3.connect('file:/var/lib/chorus/audit.sqlite?mode=ro',
uri=True); c.row_factory = sqlite3.Row; print(json.dumps([dict(r) for r in
c.execute('SELECT * FROM audit_log ORDER BY id')], ensure_ascii=False))"`.

When the period is over, delete the file (with its write-ahead log, if one is
left) from the chorus checkout, with the backend up:

```bash
docker compose -f docker/compose.yaml exec backend sh -c 'rm -f "$CHORUS_HOME/audit.sqlite" "$CHORUS_HOME/audit.sqlite-wal" "$CHORUS_HOME/audit.sqlite-shm"'
```
