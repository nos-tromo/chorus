"""Retention: deleting the posts whose ``retention_until`` has passed.

:mod:`chorus.retention.sweeper` deletes them, with every copy chorus keeps,
and records each deletion on the audit trail; :mod:`chorus.retention.report`
is the dry run that counts what a sweep would delete; ``python -m
chorus.retention.cli`` runs either. See ``docs/retention.md``.
"""
