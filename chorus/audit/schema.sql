-- §76 BDSG audit log.
-- Append-only by application contract; UPDATE/DELETE on individual rows
-- is rejected by triggers. Retention is handled by a separate privileged
-- maintenance job, not by row-level DELETE.

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,                      -- ISO-8601 UTC
  user TEXT NOT NULL,                    -- principal id
  tool_name TEXT NOT NULL,
  params_json TEXT NOT NULL,             -- JSON-serialized Pydantic input
  entities_touched_json TEXT NOT NULL,   -- JSON array of entity IDs
  result_count INTEGER NOT NULL,
  duration_ms INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ok','denied','error')),
  error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_ts        ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_user_ts   ON audit_log(user, ts);
CREATE INDEX IF NOT EXISTS idx_audit_tool_ts   ON audit_log(tool_name, ts);
CREATE INDEX IF NOT EXISTS idx_audit_status_ts ON audit_log(status, ts);

CREATE TRIGGER IF NOT EXISTS audit_no_update
  BEFORE UPDATE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'audit_log rows are immutable'); END;

CREATE TRIGGER IF NOT EXISTS audit_no_delete
  BEFORE DELETE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'audit_log rows are immutable; use the maintenance job'); END;
