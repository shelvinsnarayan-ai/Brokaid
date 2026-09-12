-- 005_auth.sql
--
-- Phase 4: users, brokerages, roles, sessions, magic-link tokens,
-- and the compliance-block projection.
--
-- NOTE: The phase-4 brief called for `003_auth.sql`; migrations 003
-- (gate) and 004 (sprint usage) were already applied, so this uses
-- the next free number.

CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE users (
    user_id      UUID PRIMARY KEY,
    email        CITEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('operator','client')),
    password_hash TEXT,                    -- argon2 hash, NULL for client users
    mfa_secret   TEXT,                     -- TOTP secret, NULL until enrolled
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE brokerages (
    brokerage_id UUID PRIMARY KEY,
    tenant_id    UUID NOT NULL REFERENCES tenants(tenant_id),
    slug         TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    licensee     TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE user_brokerage_roles (
    user_id      UUID NOT NULL REFERENCES users(user_id),
    brokerage_id UUID NOT NULL REFERENCES brokerages(brokerage_id),
    role         TEXT NOT NULL CHECK (role IN
                    ('principal','deputy','ae','accounts','compliance',
                     'pod_lead','account_manager','claims_manager','tenant_admin','auditor')),
    active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, brokerage_id, role)
);

CREATE TABLE user_client_links (
    user_id     UUID NOT NULL REFERENCES users(user_id),
    tenant_id   UUID NOT NULL REFERENCES tenants(tenant_id),
    client_id   UUID NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('client_owner','client_finance')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, tenant_id, client_id, role)
);

CREATE TABLE magic_link_tokens (
    token_hash   CHAR(64) PRIMARY KEY,
    user_id      UUID NOT NULL REFERENCES users(user_id),
    expires_at   TIMESTAMPTZ NOT NULL,
    used_at      TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE sessions (
    session_id      UUID PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(user_id),
    tenant_id       UUID NOT NULL REFERENCES tenants(tenant_id),
    brokerage_id    UUID REFERENCES brokerages(brokerage_id),
    client_id       UUID,
    active_role     TEXT NOT NULL,
    mfa_verified    BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX sessions_user_idx ON sessions (user_id) WHERE revoked_at IS NULL;
CREATE INDEX magic_link_user_idx ON magic_link_tokens (user_id) WHERE used_at IS NULL;

-- Compliance projection --------------------------------------------------
CREATE TABLE proj_active_blocks (
    tenant_id     UUID NOT NULL,
    block_id      UUID NOT NULL,
    target_kind   TEXT NOT NULL CHECK (target_kind IN ('matter','workflow')),
    target_id     TEXT NOT NULL,
    reason        TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cleared_at    TIMESTAMPTZ,
    cleared_reason TEXT,
    added_by_event   UUID REFERENCES events(event_id),
    cleared_by_event UUID REFERENCES events(event_id),
    PRIMARY KEY (tenant_id, block_id)
);
CREATE INDEX proj_active_blocks_target_idx
    ON proj_active_blocks (tenant_id, target_kind, target_id)
    WHERE cleared_at IS NULL;
ALTER TABLE proj_active_blocks ENABLE ROW LEVEL SECURITY;
ALTER TABLE proj_active_blocks FORCE  ROW LEVEL SECURITY;
CREATE POLICY proj_active_blocks_tenant_isolation ON proj_active_blocks
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
