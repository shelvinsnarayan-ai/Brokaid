-- 002_projection_matters.sql
--
-- Demonstrator projection: matters, driven by the events stream.

CREATE TABLE proj_matters (
    tenant_id         UUID NOT NULL,
    matter_id         UUID NOT NULL,
    client_id         UUID NOT NULL,
    status            TEXT NOT NULL,
    subject           TEXT NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL,
    updated_by_event  UUID NOT NULL REFERENCES events(event_id),
    PRIMARY KEY (tenant_id, matter_id)
);

ALTER TABLE proj_matters ENABLE ROW LEVEL SECURITY;
ALTER TABLE proj_matters FORCE  ROW LEVEL SECURITY;

CREATE POLICY proj_matters_tenant_isolation ON proj_matters
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
