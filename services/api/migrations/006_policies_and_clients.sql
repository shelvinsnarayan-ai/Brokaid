-- 006_policies_and_clients.sql
--
-- Phase 5: client entities, policies (Managed/Tracked),
-- and a JSONB `client_prefs` column on sessions for the dev-mode
-- jurisdiction preview.

CREATE TABLE clients (
    client_id         UUID PRIMARY KEY,
    tenant_id         UUID NOT NULL REFERENCES tenants(tenant_id),
    display_name      TEXT NOT NULL,
    industry          TEXT NOT NULL,
    domicile          TEXT NOT NULL,
    jurisdiction_code TEXT NOT NULL CHECK (jurisdiction_code IN ('AU','SG')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE clients FORCE  ROW LEVEL SECURITY;
CREATE POLICY clients_tenant_isolation ON clients
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);


CREATE TABLE policies (
    policy_id         UUID PRIMARY KEY,
    tenant_id         UUID NOT NULL REFERENCES tenants(tenant_id),
    client_id         UUID NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('managed','tracked','pending','lapsed')),
    product           TEXT NOT NULL,
    insurer_display   TEXT NOT NULL,
    inception         TIMESTAMPTZ NOT NULL,
    expiry            TIMESTAMPTZ NOT NULL,
    premium_amount    NUMERIC(12,2) NOT NULL,
    premium_currency  TEXT NOT NULL,
    schedule          JSONB NOT NULL,
    imported_from     TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX policies_client_idx ON policies (tenant_id, client_id);
ALTER TABLE policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE policies FORCE  ROW LEVEL SECURITY;
CREATE POLICY policies_tenant_isolation ON policies
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);


-- Client-app jurisdiction preview preference lives on the session.
ALTER TABLE sessions
  ADD COLUMN client_prefs JSONB NOT NULL DEFAULT '{}'::jsonb;
