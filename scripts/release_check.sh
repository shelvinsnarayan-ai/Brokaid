#!/usr/bin/env bash
# R1.0 release check from a terminal.
# Usage: scripts/release_check.sh <base-url> <olly-session-cookie-value>
#   e.g. scripts/release_check.sh https://app.example.com "$BROKAID_SESSION"
# Exit code: 0 PASS, 1 FAIL, 2 could not run.
set -euo pipefail
BASE="${1:?base URL}"; SESSION="${2:?Olly session cookie value (brokaid_session)}"
H=(-H "Cookie: brokaid_session=${SESSION}" -H "Content-Type: application/json")
start=$(curl -sS -w '\n%{http_code}' -X POST "${BASE%/}/api/o/release-check" "${H[@]}" -d '{}')
code=$(tail -n1 <<<"$start"); body=$(sed '$d' <<<"$start")
if [ "$code" != "200" ]; then echo "Could not start release check (HTTP $code): $body"; exit 2; fi
run_id=$(python3 -c 'import sys,json;print(json.load(sys.stdin)["run_id"])' <<<"$body")
echo "Release check $run_id started"
for _ in $(seq 1 80); do
  sleep 3
  run=$(curl -sS "${BASE%/}/api/o/release-check/${run_id}" "${H[@]}")
  status=$(python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])' <<<"$run")
  [ "$status" = "done" ] && break
done
python3 - "$run" <<'PY'
import json, sys
r = json.loads(sys.argv[1])
for c in r.get("checks", []):
    print(f"  {c['status'].upper():5} {c['ms']:>7}ms  {c['label']}: {c['detail']}")
print(f"RESULT {r.get('result')}  sha {r.get('short_sha')}  env {r.get('env')}  verified {r.get('verified')}")
sys.exit(0 if r.get("result") == "PASS" else 1)
PY
