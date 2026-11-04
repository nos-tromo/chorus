# retention

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
`retention_until = null`, so the nightly sweep never targets it.

A nightly sweep (separate ticket) hard-deletes expired posts and their
cascade: comments, message replies under the same posting, the post's
attachments, and any orphaned aliases. Posts with a null `retention_until`
(retention disabled) are never swept. Authors and
entities are not deleted by the sweep — including the personal profile
fields the `profiles` ingestion stage writes onto `:Author` (`bio`,
`date_of_birth`, and the rest), which are retained indefinitely by
deliberate decision. See `compliance.md` and ADR 0006.

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
