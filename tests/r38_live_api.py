"""R3.8 live-API tests against preview — client card GET, isolation, PATCH role gating."""
import os, sys, requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://brokaid-demo.preview.emergentagent.com").rstrip("/")
CLM = "a3cbd499-6a45-4ca4-9b17-f8d62b45ef40"  # CLM-2026-000013 (owner@)

def imp(email):
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/dev/impersonate", json={"email": email}, timeout=20)
    assert r.status_code == 200, (email, r.status_code, r.text[:200])
    return s

results = []
def check(name, cond, info=""):
    results.append((name, cond, info))
    print(("PASS" if cond else "FAIL"), name, info)

# 1. owner sees quote_summary with Northside, Q-1042, 2340.00
owner = imp("owner@fictionalclient.example")
r = owner.get(f"{BASE}/api/c/claims/{CLM}", timeout=20)
check("owner GET claim 200", r.status_code == 200, str(r.status_code))
j = r.json()
qs = j.get("claim", {}).get("quote_summary")
check("quote_summary present", bool(qs))
if qs:
    lat = qs.get("latest", {})
    check("latest.repairer == Northside Smash Repairs", lat.get("repairer") == "Northside Smash Repairs", repr(lat.get("repairer")))
    check("latest.total == 2340.00", lat.get("total") == "2340.00", repr(lat.get("total")))
    check("latest.quote_no == Q-1042", lat.get("quote_no") == "Q-1042", repr(lat.get("quote_no")))
    check("count >= 1", qs.get("count", 0) >= 1, str(qs.get("count")))
    fid = lat.get("file_id")
    # View quote PDF
    r2 = owner.get(f"{BASE}/api/c/claims/{CLM}/files/{fid}", timeout=20)
    check("View quote PDF 200", r2.status_code == 200, str(r2.status_code))
    check("View quote content-type pdf", "pdf" in r2.headers.get("content-type", ""), r2.headers.get("content-type", ""))

# 2. owner2 404 isolation
owner2 = imp("owner2@fictionalclient.example")
r = owner2.get(f"{BASE}/api/c/claims/{CLM}", timeout=20)
check("owner2 GET claim 404", r.status_code == 404, str(r.status_code))

# 3. PATCH role gating: operator 403, ae 422 on invalid total  (NO fix sent — don't mutate owner@ claim)
if qs:
    fid = qs["latest"]["file_id"]
    operator = imp("operator@harbourline.example")
    r = operator.patch(f"{BASE}/api/o/claims/{CLM}/files/{fid}/quote",
                       json={"repairer": "X Pty", "total": "9999.99"}, timeout=20)
    check("operator PATCH 403", r.status_code == 403, str(r.status_code))
    ae = imp("ae@harbourline.example")
    r = ae.patch(f"{BASE}/api/o/claims/{CLM}/files/{fid}/quote",
                 json={"repairer": "X", "total": "abc"}, timeout=20)
    check("ae PATCH invalid total 422", r.status_code == 422, str(r.status_code))

failed = [n for n, c, i in results if not c]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    sys.exit(1)
