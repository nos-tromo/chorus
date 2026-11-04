# compliance

Stub. See *Compliance posture* in [`CLAUDE.md`](../CLAUDE.md) for the v1
defaults (authentication enforced at the edge gateway with OIDC as the
planned swap-in, §76 BDSG query logging, per-post retention timers, DSFA
scope). This file will expand with the as-built compliance design once
the v1 features land.

## Retention

Every post is stamped with a deadline at ingestion (`retention_until`,
`RETENTION_DAYS_DEFAULT` days after chorus ingested it). The sweeper deletes
expired posts with every copy chorus keeps of them — their comment and reply
threads, the aliases, hashtags and attachments only they referenced, their
raw-store rows — plus raw rows and staged uploads that outlived the period,
and records each deletion on the audit trail before it takes effect. It
deletes nothing until an operator switches it on
(`RETENTION_SWEEP_ENABLED`, off by default and in the compose shape); the
dry run (`make retention-report`) shows first what the configured period
would delete, as counts. Copies outside chorus's stores — the operator's
source exports and data-plane's backups — follow their own owners' rules.
Details: [retention.md](retention.md).

## Profile data retention

The `profiles` ingestion stage writes personal profile fields — `bio`,
`date_of_birth`, `hometown`, `work_education`, `current_city` — onto
`:Author` nodes. By deliberate decision, this data is **not** subject to
the retention sweep: the sweep deletes posts and keeps `:Author` nodes with
their social edges and their raw `profiles` and `connections` rows (see
[`retention.md`](retention.md#what-a-sweep-keeps)), and no per-author
retention timer is set. Author profile data is therefore retained
indefinitely, also once a sweep has deleted every post of an author: the
dry run counts the authors a sweep would leave without any post or social
edge, and how many of them carry profile data.

This is a conscious choice, recorded in
[ADR 0006](decisions/0006-profiles-table.md). It must be confirmed by
the DSFA — profile fields such as date of birth are personal data and
may touch Art. 9 categories.

## Audit logging

Every processing of personal data that §76 BDSG distinguishes is recorded as
one hash-chained event on the federation's audit stream, with the actor, the
time, the stated purpose, references to what was touched and, for transfers,
the recipient:

- **query** — every tool call (REST and agent-invoked) and `GET /stats`;
- **ai_call** — every agent turn, plus the texts sent to the NER, embedding
  and tie-break models during ingestion and resolution (the transfer records);
- **collect** — every ingestion pass, UI upload or CLI, including the rows
  written to the raw store;
- **combine** — every entity-resolution run;
- **modify** — data-changing migrations, the norm-key backfill, and links
  that resolution replaces;
- **delete** — every deletion by the retention sweep (expired posts with
  their threads and what only they referenced, their raw rows, raw rows no
  post holds, stale staged uploads), recorded before it takes effect, and the
  deletion of a staged upload once its job is over.

Writes fail closed: an operation whose event cannot be written fails.
Queries and disclosures must state their reason: the compose shape refuses
them without a stated purpose (`AUDIT_REQUIRE_PURPOSE=query,disclose`), and
the SPA's users give it in the purpose bar in the header. The
per-endpoint and per-command detail, the stated-purpose rule
(`X-Audit-Purpose`, `AUDIT_REQUIRE_PURPOSE`) and what is deliberately not
recorded are in [audit.md](audit.md); the decision record is
[ADR 0018](decisions/0018-audit-events-on-the-federation-stream.md). The store,
its retention and who may read it are obs-plane's (obs-plane's
[`docs/audit.md`](https://github.com/nos-tromo/obs-plane/blob/main/docs/audit.md)).

The SQLite audit log that releases before 0.8.0 wrote is frozen on the
`chorus-state` volume; its rows are kept until the end of the calendar year
after the last row was written and then deleted by the operator — see
[retention.md](retention.md#legacy-sqlite-audit-log) for reading and deleting
it.
