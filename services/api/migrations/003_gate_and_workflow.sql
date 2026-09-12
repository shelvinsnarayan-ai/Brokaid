-- 003_gate_and_workflow.sql
--
-- Phase 2 projections: class unlock protocol + workflow waits.

CREATE TABLE proj_class_unlock (
    tenant_id         UUID NOT NULL,
    class_key         TEXT NOT NULL,
    decisions_seen    INT  NOT NULL DEFAULT 0,
    agreements        INT  NOT NULL DEFAULT 0,
    eno_misses        INT  NOT NULL DEFAULT 0,
    unlocked          BOOLEAN NOT NULL DEFAULT FALSE,
    unlocked_at       TIMESTAMPTZ,
    last_relock_at    TIMESTAMPTZ,
    last_relock_reason TEXT,
    updated_by_event  UUID REFERENCES events(event_id),
    PRIMARY KEY (tenant_id, class_key)
);
ALTER TABLE proj_class_unlock ENABLE ROW LEVEL SECURITY;
ALTER TABLE proj_class_unlock FORCE  ROW LEVEL SECURITY;
CREATE POLICY proj_class_unlock_tenant_isolation ON proj_class_unlock
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);


CREATE TABLE proj_workflow_waits (
    tenant_id         UUID NOT NULL,
    workflow_id       TEXT NOT NULL,
    step              TEXT NOT NULL,
    resume_predicate  JSONB NOT NULL,
    fire_at           TIMESTAMPTZ,
    timeout_at        TIMESTAMPTZ,
    resolved          BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at       TIMESTAMPTZ,
    updated_by_event  UUID REFERENCES events(event_id),
    PRIMARY KEY (tenant_id, workflow_id, step)
);
CREATE INDEX proj_workflow_waits_fire_at
  ON proj_workflow_waits (fire_at)
  WHERE NOT resolved;
ALTER TABLE proj_workflow_waits ENABLE ROW LEVEL SECURITY;
ALTER TABLE proj_workflow_waits FORCE  ROW LEVEL SECURITY;
CREATE POLICY proj_workflow_waits_tenant_isolation ON proj_workflow_waits
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
