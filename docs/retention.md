# retention

`Post.retention_until` is set at ingestion from `RETENTION_DAYS_DEFAULT`. A
nightly sweep (separate ticket) hard-deletes expired posts and their
cascade: comments, message replies under the same posting, the post's
attachments, and any orphaned aliases. Authors and entities are not
deleted by the sweep.

The audit log has its own retention (`AUDIT_RETENTION_DAYS`) and is
maintained by a separate privileged job, not by row-level DELETE — the
audit table has triggers that reject UPDATE/DELETE on individual rows.
