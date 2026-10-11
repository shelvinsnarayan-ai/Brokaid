#!/usr/bin/env bash
# Bake the current git HEAD SHA into /app/backend/.build_sha so
# /api/build-info returns a real value inside Docker containers
# where .git has been stripped (R1 #1 tester follow-up).
#
# Deploy pipelines should run this AT BUILD TIME, before the
# Dockerfile COPYs /app into the image, e.g.:
#
#   RUN /app/scripts/bake_build_sha.sh
#
# The output file is a plain-text sha (single line, 40 hex chars).
# /api/build-info prefers env `BROKAID_BUILD_SHA` if set, then this
# file, then runtime `git rev-parse HEAD`.
set -euo pipefail

target="${BROKAID_BUILD_SHA_FILE:-/app/backend/.build_sha}"

if command -v git >/dev/null 2>&1 && git -C /app rev-parse HEAD >/dev/null 2>&1; then
    sha="$(git -C /app rev-parse HEAD)"
elif [ -n "${BROKAID_BUILD_SHA:-}" ]; then
    sha="${BROKAID_BUILD_SHA}"
else
    echo "bake_build_sha: no git repo and no BROKAID_BUILD_SHA env" >&2
    exit 1
fi

printf '%s\n' "${sha}" > "${target}"
echo "wrote ${target} = ${sha}"
