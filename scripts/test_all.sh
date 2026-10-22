#!/usr/bin/env bash
# R1.3 one-command gate. Usage: scripts/test_all.sh [--fast]
#   full : backend lint + pytest (parallel, throwaway DBs), frontend typecheck + build, deny-list scans, chain verify
#   fast : lint + typecheck + source scans only (used by the pre-commit hook)
# Live LLM tests stay opt-in (BROKAID_LLM_LIVE_TESTS=true).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
MODE="${1:-full}"; WORKERS="${TEST_WORKERS:-4}"
declare -a NAMES=() RESULTS=() TIMES=()
FAIL=0; T0=$(date +%s); LOGDIR="/tmp/test_all/$(date +%Y%m%d-%H%M%S)"; mkdir -p "$LOGDIR"

step() {  # step <name> <command...>
  local name="$1"; shift; local s=$(date +%s); local log="$LOGDIR/${name//[ ()]/_}.log"
  printf '>> %-28s' "$name"
  if "$@" >"$log" 2>&1; then RESULTS+=("PASS"); echo " PASS"; else RESULTS+=("FAIL"); FAIL=1; echo " FAIL (see $log)"; tail -n 25 "$log"; fi
  NAMES+=("$name"); TIMES+=($(( $(date +%s) - s )))
}

mongo_preflight() {  # mongod aborts with EMFILE (errno 24) at the default 1024 open-file limit
  local pid; pid=$(pgrep -x mongod | head -1) || { echo "mongod not running"; return 1; }
  local soft; soft=$(awk '/Max open files/{print $4}' /proc/"$pid"/limits)
  if [ "$soft" -lt 65536 ]; then sudo -n prlimit --pid "$pid" --nofile=65536:1048576 || prlimit --pid "$pid" --nofile=65536:1048576; fi
  awk '/Max open files/{print "mongod open-file limit:", $4}' /proc/"$pid"/limits
}

step "mongo preflight" mongo_preflight
step "secrets scan (tree+history)" bash scripts/secrets_scan.sh
step "dependency audit" bash scripts/dep_audit.sh
step "backend lint (ruff E9,F)" bash -c "cd backend && ruff check app tests --select E9,F"
step "frontend typecheck" bash -c "cd frontend && node_modules/.bin/tsc --noEmit -p ."
step "deny-list (source)" node ci/deny_list_source.js
step "events append-only scan" node ci/mongo_events_write_scan.js backend/app
step "raw collection scan" node ci/mongo_raw_collection_scan.js
step "ts_override scan" node ci/ts_override_scan.js .

if [ "$MODE" != "--fast" ]; then
  step "backend pytest (-n $WORKERS)" bash -c "cd backend && python -m pytest tests -q -p no:warnings -n $WORKERS -p no:cacheprovider"
  step "frontend build" bash -c "cd frontend && yarn -s build"
  step "deny-list (bundle)" node ci/deny-list.js frontend/dist
  step "pwa bundle (service worker)" node ci/pwa_bundle_check.js frontend/dist
  step "chain verify (read-only)" bash -c "cd backend && set -a && MONGO_URL=\$(grep ^MONGO_URL= .env | cut -d= -f2- | tr -d '\"') DB_NAME=\$(grep ^DB_NAME= .env | cut -d= -f2- | tr -d '\"') python ../scripts/chain_verify_nightly.py"
fi

echo; echo "==================== test_all summary ($MODE) ===================="
for i in "${!NAMES[@]}"; do printf '  %-5s %5ss  %s\n' "${RESULTS[$i]}" "${TIMES[$i]}" "${NAMES[$i]}"; done
PY="$LOGDIR/backend_pytest__-n_${WORKERS}_.log"
[ -f "$PY" ] && echo "  pytest: $(grep -E '[0-9]+ (passed|failed)' "$PY" | tail -1)"
echo "  logs: $LOGDIR"; echo "  total runtime: $(( $(date +%s) - T0 ))s"
if [ $FAIL -eq 0 ]; then
  echo "RESULT GREEN"
  [ "$MODE" != "--fast" ] && (git rev-parse HEAD; git diff HEAD) | sha256sum | cut -d' ' -f1 > .git/brokaid_full_green
  exit 0
fi
echo "RESULT RED"; exit 1
