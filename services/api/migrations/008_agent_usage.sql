-- 008_agent_usage.sql
--
-- Phase 8: per-agent token usage projection (fed by AgentOutputProduced).
CREATE TABLE proj_sprint_usage_by_agent (
    tenant_id           UUID NOT NULL,
    agent_name          TEXT NOT NULL,
    tokens_used_input   BIGINT NOT NULL DEFAULT 0,
    tokens_used_output  BIGINT NOT NULL DEFAULT 0,
    invocations         INTEGER NOT NULL DEFAULT 0,
    first_seen          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, agent_name)
);
CREATE INDEX proj_sprint_usage_by_agent_last_seen
  ON proj_sprint_usage_by_agent (tenant_id, last_seen DESC);
ALTER TABLE proj_sprint_usage_by_agent ENABLE ROW LEVEL SECURITY;
ALTER TABLE proj_sprint_usage_by_agent FORCE  ROW LEVEL SECURITY;
CREATE POLICY proj_sprint_usage_by_agent_tenant_isolation
  ON proj_sprint_usage_by_agent
  USING       (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK  (tenant_id = current_setting('app.tenant_id', true)::uuid);
