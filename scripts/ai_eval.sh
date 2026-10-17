#!/usr/bin/env bash
# R1.7 AI eval gate: golden set through intake (live LLM) + guard + classifier. Exit 0 = gate PASS.
set -euo pipefail
cd "$(dirname "$0")/../backend"
exec python -m app.eval.run "$@"
