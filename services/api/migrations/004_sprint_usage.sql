-- 004_sprint_usage.sql
--
-- Phase 3 projection: per-tenant sprint token usage.
--
-- Aggregate tokens_used and invocation count from AgentOutputProduced.
-- last_alert_pct is the highest SprintCapAlert threshold already emitted
-- for the tenant so Brain can skip re-firing the same alert.

CREATE TABLE proj_sprint_usage (
    tenant_id         UUID PRIMARY KEY,
    tokens_used       BIGINT NOT NULL DEFAULT 0,
    invocations       BIGINT NOT NULL DEFAULT 0,
    last_alert_pct    INT    NOT NULL DEFAULT 0,
    updated_by_event  UUID REFERENCES events(event_id)
);
ALTER TABLE proj_sprint_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE proj_sprint_usage FORCE  ROW LEVEL SECURITY;
CREATE POLICY proj_sprint_usage_tenant_isolation ON proj_sprint_usage
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
