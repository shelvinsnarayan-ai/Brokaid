#!/usr/bin/env bash
# infra/postgres/snapshots/generate.sh
#
# Capture a full pg_dump snapshot of the current brokAId database and
# gzip it to infra/postgres/snapshots/<label>.sql.gz. Used by the
# CI job `verify-migration-durability` to prove that migrations run
# clean against a real DB from a prior phase.
#
# Usage:
#   ./infra/postgres/snapshots/generate.sh <label>
#
# Example:
#   ./infra/postgres/snapshots/generate.sh phase11
#   ./infra/postgres/snapshots/generate.sh phase12
#
# Reads DATABASE_URL from services/api/.env.
set -euo pipefail

LABEL="${1:?usage: generate.sh <label>}"
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
OUT_DIR="$ROOT/infra/postgres/snapshots"
OUT="$OUT_DIR/$LABEL.sql.gz"

if [[ -z "${DATABASE_URL:-}" ]]; then
    DATABASE_URL="$(grep '^DATABASE_URL' "$ROOT/services/api/.env" | cut -d= -f2-)"
fi
export DATABASE_URL

mkdir -p "$OUT_DIR"
PG_DUMP="${PG_DUMP:-}"
if [[ -z "$PG_DUMP" ]]; then
    if command -v pg_dump >/dev/null 2>&1; then
        PG_DUMP="pg_dump"
    else
        for cand in /usr/lib/postgresql/*/bin/pg_dump; do
            [[ -x "$cand" ]] && PG_DUMP="$cand" && break
        done
    fi
fi
if [[ -z "$PG_DUMP" ]] || ! [[ -x "$(command -v "$PG_DUMP" 2>/dev/null || echo "$PG_DUMP")" ]]; then
    echo "ERROR: pg_dump not found (looked in PATH and /usr/lib/postgresql/*/bin)" >&2
    exit 2
fi
echo "Dumping $DATABASE_URL → $OUT (using $PG_DUMP) ..."
# pg_dump needs to bypass RLS. Connect as the local postgres superuser
# (Unix socket auth) which has BYPASSRLS. If DATABASE_URL points at a
# non-superuser, override for the dump only.
DUMP_DSN="${SNAPSHOT_DSN:-postgresql://postgres@/brokaid?host=/var/run/postgresql}"
if ! su - postgres -c "$PG_DUMP -d brokaid --schema-only 2>&1 | head -1" >/dev/null 2>&1; then
    DUMP_DSN="$DATABASE_URL"
fi
su - postgres -c "$PG_DUMP \
    --no-owner \
    --no-privileges \
    --if-exists --clean \
    -d brokaid" | gzip -9 > "$OUT"

SIZE=$(stat -c%s "$OUT")
SHA=$(sha256sum "$OUT" | cut -d' ' -f1)
echo "  size:   $SIZE bytes"
echo "  sha256: $SHA"
echo "  wrote:  $OUT"
