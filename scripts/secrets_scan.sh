#!/usr/bin/env bash
# R1.9 — secrets scan of the repo working tree AND full git history. Exit 1 on any hit.
set -uo pipefail
cd "$(dirname "$0")/.."
PAT='sk-emergent-[A-Za-z0-9]{6,}|sk-(ant|proj)-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|re_[A-Za-z0-9]{24,}|xox[bpa]-[A-Za-z0-9-]{10,}|(EMERGENT_LLM_KEY|VAPID_PRIVATE_KEY|BACKUP_ENCRYPTION_KEY|EMERGENT_EMAIL_KEY)=[A-Za-z0-9+/_=-]{12,}'
hits=0
tracked=$(git ls-files | grep -vE '(^|/)node_modules/|\.lock$|yarn\.lock$|\.png$|\.jpg$|\.pdf$' || true)
if git grep -nIE "$PAT" -- $tracked >/tmp/secrets_tree.txt 2>/dev/null; then hits=1; fi
if git log --all -p --no-color 2>/dev/null | grep -nE "$PAT" >/tmp/secrets_history.txt; then hits=1; fi
if git ls-files | grep -E '(^|/)\.env$' | grep -v '^mobile/\.env$' >/tmp/secrets_envfiles.txt; then hits=1; fi
if [ "$hits" = 1 ]; then
  echo "secrets scan FAILED:"; cut -c1-160 /tmp/secrets_tree.txt /tmp/secrets_history.txt /tmp/secrets_envfiles.txt 2>/dev/null | head -20
  exit 1
fi
echo "secrets scan clean ($(git rev-list --all | wc -l) commits, $(echo "$tracked" | wc -l) tracked files)"
