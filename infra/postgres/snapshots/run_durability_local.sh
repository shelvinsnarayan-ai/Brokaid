#!/usr/bin/env bash
# Phase 12.6 — local run of verify-migration-durability.yml.
# Bounded, exits non-zero on any assertion failure.

set -o pipefail

SCRATCH_DB="brokaid_durability"
DSN_ADMIN="postgresql://brokaid:brokaid_dev_local@localhost:5432/postgres"
DSN_SCRATCH="postgresql://brokaid:brokaid_dev_local@localhost:5432/${SCRATCH_DB}"
PORT=8002
PSQL="/usr/lib/postgresql/15/bin/psql"
API_LOG=/tmp/durability_api.log

log() { printf '[durability] %s\n' "$*"; }
fail() { printf '[durability] FAIL: %s\n' "$*" >&2; exit 1; }

cleanup() {
  log "tearing down"
  if [[ -n "${API_PID:-}" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
  "$PSQL" "$DSN_ADMIN" -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${SCRATCH_DB}' AND pid <> pg_backend_pid()" >/dev/null 2>&1 || true
  "$PSQL" "$DSN_ADMIN" -c "DROP DATABASE IF EXISTS ${SCRATCH_DB}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# 1. Fresh scratch DB
log "creating scratch DB ${SCRATCH_DB}"
"$PSQL" "$DSN_ADMIN" -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${SCRATCH_DB}' AND pid <> pg_backend_pid()" >/dev/null 2>&1 || true
"$PSQL" "$DSN_ADMIN" -c "DROP DATABASE IF EXISTS ${SCRATCH_DB}" >/dev/null || true
"$PSQL" "$DSN_ADMIN" -c "CREATE DATABASE ${SCRATCH_DB} OWNER brokaid" >/dev/null || fail "create db"

# 2. Restore phase-11 snapshot
log "restoring phase-11 snapshot"
gunzip -c /app/infra/postgres/snapshots/phase11.sql.gz | "$PSQL" "$DSN_SCRATCH" >/tmp/restore.log 2>&1
RESTORE_STATUS=$?
if [[ $RESTORE_STATUS -ne 0 ]]; then
  log "restore stderr tail:"; tail -20 /tmp/restore.log
  fail "psql restore returned ${RESTORE_STATUS}"
fi

# 3. Apply migrations against the scratch DB
log "applying migrations"
cd /app/services/api
DATABASE_URL="$DSN_SCRATCH" python -m app.migrate 2>&1 | sed 's/^/[migrate] /'
MIGRATE_STATUS=${PIPESTATUS[0]}
[[ $MIGRATE_STATUS -eq 0 ]] || fail "migrate exit=${MIGRATE_STATUS}"

# 4. Boot API on port ${PORT} pointed at scratch DB
log "booting API on :${PORT}"
DATABASE_URL="$DSN_SCRATCH" \
  AUTH_DEV_MODE=true \
  ENVIRONMENT=development \
  ADMIN_BUNDLE_PATH="" \
  nohup /root/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT" \
    > "$API_LOG" 2>&1 &
API_PID=$!
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${PORT}/api/healthz" >/dev/null 2>&1; then
    log "API up (pid=${API_PID})"; break
  fi
  sleep 1
done
if ! curl -sf "http://127.0.0.1:${PORT}/api/healthz" >/dev/null; then
  log "API log tail:"; tail -40 "$API_LOG"
  fail "API failed to start"
fi

# 5. Public verifier smoke — expect 4xx, fail on 5xx
log "verifier smoke test"
RANDOM_TOKEN="unknown-$(date +%s)-$RANDOM"
VERIFIER_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  "http://127.0.0.1:${PORT}/api/verifier/${RANDOM_TOKEN}")
log "verifier status: ${VERIFIER_STATUS}"
case "${VERIFIER_STATUS}" in
  401|403|404) log "OK — expected 4xx for unknown token" ;;
  5*) fail "verifier returned ${VERIFIER_STATUS} (5xx = durability break)" ;;
  2*) fail "verifier returned ${VERIFIER_STATUS} without seeded raw token" ;;
  *)  fail "verifier returned unexpected status ${VERIFIER_STATUS}" ;;
esac

# 6. Operator queue smoke — dev-mode session
log "operator queue smoke"
COOKIE_JAR=/tmp/durability_cookie.jar
rm -f "$COOKIE_JAR"
IMP_STATUS=$(curl -s -o /tmp/imp.json -w "%{http_code}" \
  -c "$COOKIE_JAR" -X POST "http://127.0.0.1:${PORT}/api/auth/dev/impersonate" \
  -H "Content-Type: application/json" \
  -d '{"email":"principal@burmac.example"}')
log "impersonate status: ${IMP_STATUS}"
[[ "$IMP_STATUS" == "200" ]] || { cat /tmp/imp.json; fail "impersonate failed"; }

# Look up a matter to hit /api/o/queue/{matter_id} — queue route requires
# matter_id in some code paths. Try the base queue first; if 4xx, pull a
# matter from the snapshot.
Q_STATUS=$(curl -s -o /tmp/q.json -w "%{http_code}" \
  -b "$COOKIE_JAR" "http://127.0.0.1:${PORT}/api/o/queue")
log "queue base status: ${Q_STATUS}"
if [[ "$Q_STATUS" == "404" || "$Q_STATUS" == "422" ]]; then
  # Try with a snapshot-known matter_id.
  MATTER_ID=$("$PSQL" "$DSN_SCRATCH" -tAc \
    "SELECT matter_id::text FROM proj_matters LIMIT 1")
  log "retrying queue with matter_id=${MATTER_ID}"
  Q_STATUS=$(curl -s -o /tmp/q.json -w "%{http_code}" \
    -b "$COOKIE_JAR" "http://127.0.0.1:${PORT}/api/o/queue/${MATTER_ID}")
  log "queue matter status: ${Q_STATUS}"
fi
[[ "$Q_STATUS" == "200" ]] || { cat /tmp/q.json; fail "queue non-200 (${Q_STATUS})"; }
ROWS=$(python3 -c "import sys,json;d=json.load(open('/tmp/q.json'));print(len(d.get('rows', d if isinstance(d,list) else [])))")
log "queue rows: ${ROWS}"

log "DURABILITY_WORKFLOW_PASS verifier=${VERIFIER_STATUS} queue=${Q_STATUS} rows=${ROWS}"
