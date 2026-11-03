"""Data-protection audit trail (§76 BDSG).

``trail`` writes the process's hash-chained audit events (the federation
audit stream); ``cli`` runs one CLI command in an audit chain of its own.
``logger`` is the legacy SQLite log, which nothing writes any more.
"""
