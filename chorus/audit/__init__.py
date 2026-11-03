"""Data-protection audit trail (§76 BDSG).

``trail`` writes the process's hash-chained audit events (the federation
audit stream). ``logger`` is the legacy SQLite log, still written by the
ingestion routes.
"""
