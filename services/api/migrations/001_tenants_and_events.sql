-- 001_tenants_and_events.sql
--
-- The trAIl append-only ledger — per-tenant SHA-256 hash chain,
-- BEFORE UPDATE/DELETE triggers, RLS.
--
-- NOTE: Genesis hash on tenants is set exactly once during
-- create_tenant() (app-level, one transaction).  The trigger below
-- lets it move from the placeholder `'0'*64` to a real hash, and
-- freezes it forever thereafter.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE tenants (
    tenant_id     UUID PRIMARY KEY,
    slug          TEXT UNIQUE NOT NULL,
    display_name  TEXT NOT NULL,
    genesis_hash  CHAR(64) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION tenants_immutable_genesis() RETURNS TRIGGER AS $$
BEGIN
  IF OLD.genesis_hash <> repeat('0', 64)
     AND NEW.genesis_hash IS DISTINCT FROM OLD.genesis_hash THEN
    RAISE EXCEPTION 'tenants.genesis_hash is immutable once set';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tenants_immutable_genesis_trg
  BEFORE UPDATE ON tenants
  FOR EACH ROW EXECUTE FUNCTION tenants_immutable_genesis();


CREATE TABLE events (
    event_id         UUID PRIMARY KEY,
    tenant_id        UUID NOT NULL REFERENCES tenants(tenant_id),
    ts               TIMESTAMPTZ NOT NULL,
    actor            JSONB NOT NULL,
    type             TEXT NOT NULL,
    payload          JSONB NOT NULL,
    payload_jcs      TEXT NOT NULL,
    prev_hash        CHAR(64) NOT NULL,
    event_hash       CHAR(64) NOT NULL,
    idempotency_key  TEXT NOT NULL,
    mocked           BOOLEAN NOT NULL DEFAULT FALSE,
    seq              BIGSERIAL NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX events_tenant_idempotency
    ON events (tenant_id, idempotency_key);
CREATE INDEX events_tenant_seq ON events (tenant_id, seq);
CREATE INDEX events_type       ON events (type);

CREATE OR REPLACE FUNCTION events_deny_mutation() RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'events table is append-only (attempted %) on event %',
    TG_OP, COALESCE(OLD.event_id::text, 'unknown');
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER events_no_update BEFORE UPDATE ON events
  FOR EACH ROW EXECUTE FUNCTION events_deny_mutation();
CREATE TRIGGER events_no_delete BEFORE DELETE ON events
  FOR EACH ROW EXECUTE FUNCTION events_deny_mutation();

-- Row-Level Security per tenant.
-- FORCE so the owner (brokaid) can't bypass. The GUC `app.tenant_id`
-- is set at the start of every request/transaction that touches events.
ALTER TABLE events ENABLE ROW LEVEL SECURITY;
ALTER TABLE events FORCE  ROW LEVEL SECURITY;

CREATE POLICY events_tenant_isolation ON events
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
