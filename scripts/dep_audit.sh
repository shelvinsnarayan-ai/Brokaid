#!/usr/bin/env bash
# R1.9 — dependency vulnerability scan: pip-audit (installed backend env) + yarn audit (frontend prod deps).
# Fails on HIGH/CRITICAL (yarn) or any fixable Python vuln, except ids listed with a reason in ci/audit_allowlist.txt.
set -uo pipefail
cd "$(dirname "$0")/.."
ALLOW=$(python3 ci/audit_allowlist.py)  # only entries with a reason and a review date that hasn't passed
fail=0
( cd backend && timeout 240 pip-audit --local --format json >/tmp/pipaudit.json 2>/tmp/pipaudit.err ) || true
python3 - "$ALLOW" <<'PY' || fail=1
import json, sys
allow = set(sys.argv[1].split())
try:
    d = json.load(open("/tmp/pipaudit.json"))
except Exception:
    print("pip-audit produced no report:", open("/tmp/pipaudit.err").read()[-300:]); sys.exit(1)
bad = [(x["name"], x["version"], v["id"], v.get("fix_versions")) for x in d.get("dependencies", []) for v in x.get("vulns", [])
       if v["id"] not in allow and not any(a in allow for a in v.get("aliases", []))]
for b in bad: print("python vuln:", *b)
print(f"pip-audit: {len(bad)} unallowed finding(s)")
sys.exit(1 if bad else 0)
PY
( cd frontend && timeout 240 yarn audit --json --groups dependencies >/tmp/yarnaudit.json 2>/dev/null ) || true
python3 - "$ALLOW" <<'PY' || fail=1
import json, sys
allow = set(sys.argv[1].split())
bad = {}
for line in open("/tmp/yarnaudit.json"):
    try:
        j = json.loads(line)
    except ValueError:
        continue
    if j.get("type") == "auditAdvisory":
        a = j["data"]["advisory"]
        if a["severity"] in ("high", "critical") and str(a["id"]) not in allow and a.get("github_advisory_id") not in allow:
            bad[a["id"]] = (a["module_name"], a["severity"], a.get("github_advisory_id"), a["title"][:80])
for b in bad.values(): print("js vuln:", *b)
print(f"yarn audit: {len(bad)} unallowed high/critical finding(s)")
sys.exit(1 if bad else 0)
PY
v=$(cd backend && python3 -c "from app.kernel import media; print(media.ffmpeg_version() or '')" 2>/dev/null)
if [ -z "$v" ]; then echo "ffmpeg: bundled binary (imageio-ffmpeg) MISSING"; fail=1
else echo "ffmpeg: bundled $v via imageio-ffmpeg (wheel covered by pip-audit above; binary version recorded for CVE review; used only for stream-copy + one poster frame)"; fi
timeout 20 python3 ci/audit_allowlist.py watch || true  # informational: "upgrade available" when a pinned dep can move
exit $fail
