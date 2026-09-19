-- brokAId Postgres init — phase 0 placeholder.
--
-- The ledger schema (append-only tables, triggers denying UPDATE/DELETE,
-- per-tenant hash chain, RLS) lands in phase 1. This file exists so the
-- infra layout matches the brief and future migrations have somewhere
-- obvious to land.

-- No-op probe: prove the file loads.
DO $$
BEGIN
  RAISE NOTICE 'brokaid init.sql loaded (phase 0 placeholder)';
END $$;
