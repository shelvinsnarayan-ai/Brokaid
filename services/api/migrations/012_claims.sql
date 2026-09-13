-- 012_claims.sql
--
-- Phase 13: claim scene. FNOL entry → deterministic clAIms stub triage →
-- MOCKED insurer lodgement → status timeline → resolution.
--
-- Two tables:
--   claims                     - authoritative row (RLS + FORCE)
--   proj_claim_status_updates  - timeline projection (RLS + FORCE)
--
-- claim_reference is tenant-scoped human-readable (CLM-YYYY-NNNNNN),
-- but the sequence is global per-tenant and we don't need Postgres
-- SEQUENCES for it — the route computes it from the max existing
-- reference in a transaction with an advisory lock.

CREATE TABLE IF NOT EXISTS claims (
  claim_id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  client_id UUID NOT NULL,
  policy_id UUID,
  claim_reference TEXT NOT NULL,
  incident_date DATE,
  description TEXT NOT NULL,
  claim_type TEXT,
  urgency TEXT,
  status TEXT NOT NULL,
  opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  resolved_at TIMESTAMPTZ,
  opened_by_event UUID NOT NULL REFERENCES events(event_id),
  UNIQUE (tenant_id, claim_reference)
);
CREATE INDEX IF NOT EXISTS claims_client_idx ON claims (tenant_id, client_id);
CREATE INDEX IF NOT EXISTS claims_status_idx ON claims (tenant_id, status);

ALTER TABLE claims ENABLE ROW LEVEL SECURITY;
ALTER TABLE claims FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS claims_tenant_isolation ON claims;
CREATE POLICY claims_tenant_isolation ON claims
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

CREATE TABLE IF NOT EXISTS proj_claim_status_updates (
  claim_id UUID NOT NULL,
  tenant_id UUID NOT NULL,
  seq BIGSERIAL NOT NULL,
  event_id UUID NOT NULL REFERENCES events(event_id),
  ts TIMESTAMPTZ NOT NULL,
  kind TEXT NOT NULL,
  summary TEXT NOT NULL,
  mocked BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (claim_id, seq)
);
CREATE INDEX IF NOT EXISTS proj_claim_status_updates_tenant_idx
  ON proj_claim_status_updates (tenant_id, claim_id, seq DESC);

ALTER TABLE proj_claim_status_updates ENABLE ROW LEVEL SECURITY;
ALTER TABLE proj_claim_status_updates FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS proj_claim_status_updates_tenant_isolation
  ON proj_claim_status_updates;
CREATE POLICY proj_claim_status_updates_tenant_isolation
  ON proj_claim_status_updates
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

-- Notifications tray projection. In-app rendering reads from here.
CREATE TABLE IF NOT EXISTS proj_notifications (
  notification_id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  client_id UUID NOT NULL,
  source_event_id UUID NOT NULL REFERENCES events(event_id),
  ts TIMESTAMPTZ NOT NULL,
  kind TEXT NOT NULL,
  subject TEXT NOT NULL,
  body_text TEXT NOT NULL,
  read_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS proj_notifications_client_idx
  ON proj_notifications (tenant_id, client_id, ts DESC);

ALTER TABLE proj_notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE proj_notifications FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS proj_notifications_tenant_isolation ON proj_notifications;
CREATE POLICY proj_notifications_tenant_isolation ON proj_notifications
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
