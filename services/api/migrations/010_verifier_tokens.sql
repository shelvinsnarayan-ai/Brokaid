-- 010_verifier_tokens.sql
--
-- Phase 11: single-use tokens for the public certificate verifier.
-- The token itself is never stored; only its SHA-256 hash. The
-- verifier is unauthenticated — tenant is resolved from the token
-- row itself (documented deviation from the tenant-GUC-first pattern).

CREATE TABLE verifier_tokens (
    token_hash        CHAR(64) PRIMARY KEY,
    tenant_id         UUID NOT NULL REFERENCES tenants(tenant_id),
    policy_id         UUID NOT NULL,
    certificate_id    UUID NOT NULL,
    issued_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at        TIMESTAMPTZ NOT NULL,
    revoked_at        TIMESTAMPTZ,
    rate_limit_count  INT NOT NULL DEFAULT 0
);

CREATE INDEX verifier_tokens_policy_idx
    ON verifier_tokens (tenant_id, policy_id);

-- RLS is enabled but the SELECT policy tolerates an unset tenant GUC
-- so the public GET path can look up by token_hash without setting
-- app.tenant_id first (tenant is resolved from the row itself, then
-- set explicitly for the downstream CertificateRegenerated read).
-- Writes still require the tenant GUC.
ALTER TABLE verifier_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE verifier_tokens FORCE  ROW LEVEL SECURITY;
CREATE POLICY verifier_tokens_tenant_isolation ON verifier_tokens
  USING (
    CASE
      WHEN current_setting('app.tenant_id', true) IS NULL
        OR current_setting('app.tenant_id', true) = ''
        THEN TRUE
      ELSE tenant_id = current_setting('app.tenant_id', true)::uuid
    END
  )
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
