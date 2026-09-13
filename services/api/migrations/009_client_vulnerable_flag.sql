-- 009_client_vulnerable_flag.sql
--
-- Phase 11: flag clients that require operator review on any
-- outbound comms. Default FALSE (existing clients unchanged).

ALTER TABLE clients
  ADD COLUMN IF NOT EXISTS vulnerable_flag BOOLEAN NOT NULL DEFAULT FALSE;
