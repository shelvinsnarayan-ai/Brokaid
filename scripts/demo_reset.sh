#!/usr/bin/env bash
# Demo reset for rehearsals (R0.5). Usage: scripts/demo_reset.sh [kai|all_tests|full] [operator-email]
set -euo pipefail
SCOPE="${1:-kai}"
EMAIL="${2:-operator@harbourline.example}"  # R2.6: demo reset is Operator-only
API="${REACT_APP_BACKEND_URL:-$(grep '^REACT_APP_BACKEND_URL=' "$(dirname "$0")/../frontend/.env" | cut -d= -f2-)}"
JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT
curl -sf -c "$JAR" -X POST "$API/api/auth/dev/impersonate" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\"}" >/dev/null
curl -sf -b "$JAR" -X POST "$API/api/o/demo/reset" -H 'Content-Type: application/json' \
  -d "{\"scope\":\"$SCOPE\"}"
echo
