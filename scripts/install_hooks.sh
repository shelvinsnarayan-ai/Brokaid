#!/usr/bin/env bash
# Installs the local git hooks (R1.3). Re-run after cloning.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; H="$ROOT/.git/hooks"
cat > "$H/pre-commit" <<'EOF'
#!/usr/bin/env bash
# fast subset: lint, typecheck, source scans (~20s). Skip once with BROKAID_SKIP_HOOKS=1.
[ "${BROKAID_SKIP_HOOKS:-}" = "1" ] && exit 0
"$(git rev-parse --show-toplevel)/scripts/eval_gate_check.sh" || exit 1
exec "$(git rev-parse --show-toplevel)/scripts/test_all.sh" --fast
EOF
cat > "$H/commit-msg" <<'EOF'
#!/usr/bin/env bash
# A release commit ("release ..." or "R1.x release ...") needs a full green test_all.sh run on exactly this change.
subject=$(head -n1 "$1")
echo "$subject" | grep -qiE '^(release\b|R[0-9.]+[a-z]? release\b)' || exit 0
want=$( (git rev-parse HEAD; git diff --cached HEAD) | sha256sum | cut -d' ' -f1)
have=$(cat "$(git rev-parse --git-dir)/brokaid_full_green" 2>/dev/null || true)
[ "$want" = "$have" ] && exit 0
echo "Release commit blocked: run scripts/test_all.sh (full) on the staged change first (stage everything, then run)."
exit 1
EOF
chmod +x "$H/pre-commit" "$H/commit-msg" "$ROOT/scripts/test_all.sh"
echo "hooks installed: pre-commit (fast subset), commit-msg (release commits need a full green run)"
