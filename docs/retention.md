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

`ingested_at` is written `ON CREATE` only, so re-ingesting a row keeps its
original deadline (retention is measured from first ingestion).
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

The audit log has its own retention (`AUDIT_RETENTION_DAYS`) and is
maintained by a separate privileged job, not by row-level DELETE — the
audit table has triggers that reject UPDATE/DELETE on individual rows.
