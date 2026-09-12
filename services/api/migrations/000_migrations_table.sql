-- 000_migrations_table.sql
--
-- Applied-migration bookkeeping. Kept out of RLS so the runner can
-- inspect it without setting a tenant GUC.

CREATE TABLE IF NOT EXISTS _migrations (
    name        TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sha256      CHAR(64) NOT NULL
);
