-- 011_verifier_tokens_rls_fix.sql
--
-- Phase 11 hotfix: re-apply the CASE-guarded RLS policy on
-- verifier_tokens. The initial 010 file was edited in-flight before
-- commit, but our migrate runner is filename-based and skips any
-- migration whose name was already recorded. This idempotent DROP +
-- CREATE guarantees every DB (including previously-migrated ones)
-- ends up with the correct USING clause.
--
-- The public verifier lookup runs WITHOUT app.tenant_id set (tenant
-- resolves from the token row itself). The CASE guard skips the uuid
-- cast when the GUC is unset. Writes still enforce the GUC via
-- WITH CHECK.

DROP POLICY IF EXISTS verifier_tokens_tenant_isolation ON verifier_tokens;

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
