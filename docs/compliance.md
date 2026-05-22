# compliance

Stub. See *Compliance posture* in [`CLAUDE.md`](../CLAUDE.md) for the v1
defaults (OIDC auth, §76 BDSG query logging, per-post retention timers,
DSFA scope). This file will expand with the as-built compliance design
once the v1 features land.

## Profile data retention

The `profiles` ingestion stage writes personal profile fields — `bio`,
`date_of_birth`, `hometown`, `work_education`, `current_city` — onto
`:Author` nodes. By deliberate decision, this data is **not** subject to
the nightly retention sweep: the sweep operates on `:Post` nodes and
already skips `:Author` (see [`retention.md`](retention.md)), and no
per-author retention timer is set. Author profile data is therefore
retained indefinitely.

This is a conscious choice, recorded in
[ADR 0006](decisions/0006-profiles-table.md). It must be confirmed by
the DSFA — profile fields such as date of birth are personal data and
may touch Art. 9 categories.
