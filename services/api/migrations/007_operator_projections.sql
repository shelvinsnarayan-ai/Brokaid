-- 007_operator_projections.sql
--
-- Phase 7 operator projections:
--   * proj_queue         — pending HITL cards for the Control Tower
--   * proj_kpi_daily     — 12 KPI snapshots per tenant per day
--
-- Both under RLS ENABLE + FORCE per tenant.

CREATE TABLE proj_queue (
    tenant_id         UUID NOT NULL,
    envelope_id       UUID NOT NULL,
    matter_id         UUID,
    client_id         UUID,
    class_key         TEXT NOT NULL,
    proposal_ref      UUID NOT NULL REFERENCES events(event_id),
    hitl_since        TIMESTAMPTZ NOT NULL,
    sla_deadline      TIMESTAMPTZ,
    gate_reason       TEXT,
    confidence        NUMERIC(4,3),
    routed_to_role    TEXT,
    resolved_at       TIMESTAMPTZ,
    resolved_by_event UUID REFERENCES events(event_id),
    PRIMARY KEY (tenant_id, envelope_id)
);
CREATE INDEX proj_queue_pending_idx
    ON proj_queue (tenant_id, sla_deadline NULLS LAST)
    WHERE resolved_at IS NULL;
ALTER TABLE proj_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE proj_queue FORCE  ROW LEVEL SECURITY;
CREATE POLICY proj_queue_tenant_isolation ON proj_queue
  USING       (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK  (tenant_id = current_setting('app.tenant_id', true)::uuid);


CREATE TABLE proj_kpi_daily (
    tenant_id     UUID NOT NULL,
    day           DATE NOT NULL,
    kpi_id        SMALLINT NOT NULL CHECK (kpi_id BETWEEN 1 AND 12),
    value_scalar  NUMERIC(16,4) NOT NULL,
    value_meta    JSONB NOT NULL DEFAULT '{}'::jsonb,
    delta_prev    NUMERIC(16,4),
    spark_series  NUMERIC[],
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, day, kpi_id)
);
CREATE INDEX proj_kpi_daily_recent
  ON proj_kpi_daily (tenant_id, kpi_id, day DESC);
ALTER TABLE proj_kpi_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE proj_kpi_daily FORCE  ROW LEVEL SECURITY;
CREATE POLICY proj_kpi_daily_tenant_isolation ON proj_kpi_daily
  USING       (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK  (tenant_id = current_setting('app.tenant_id', true)::uuid);
