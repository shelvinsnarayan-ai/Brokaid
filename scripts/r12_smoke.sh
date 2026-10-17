set -e
cd /app/backend
B=http://localhost:8001
OUT=$(python -m app.scripts.r12_demo_invites)
echo "$OUT" | cut -c1-60
TOK=$(echo "$OUT" | grep "owner@fictionalclient" | grep -o "/invite/[^ ]*" | cut -d/ -f3)
J=/tmp/r12.jar; rm -f $J
echo "--- info"; curl -s $B/api/auth/invite/$TOK; echo
echo "--- weak"; curl -s -X POST $B/api/auth/invite/$TOK/accept -H 'Content-Type: application/json' -d '{"password":"password1234"}'; echo
echo "--- accept"; curl -s -c $J -X POST $B/api/auth/invite/$TOK/accept -H 'Content-Type: application/json' -d '{"password":"Harbour-Kai-Test-2026"}'; echo
echo "--- reuse"; curl -s -X POST $B/api/auth/invite/$TOK/accept -H 'Content-Type: application/json' -d '{"password":"Harbour-Kai-Test-2026"}'; echo
echo "--- pending can't use app"; curl -s -o /dev/null -w "%{http_code}\n" -b $J $B/api/c/me
SECRET=$(curl -s -b $J -c $J -X POST $B/api/auth/mfa/setup | python3 -c "import sys,json;print(json.load(sys.stdin)['secret'])")
CODE=$(python3 -c "import pyotp;print(pyotp.TOTP('$SECRET').now())")
echo "--- confirm"; curl -s -b $J -c $J -X POST $B/api/auth/mfa/confirm -H 'Content-Type: application/json' -d "{\"code\":\"$CODE\"}" | tee /tmp/r12_confirm.json | cut -c1-120; echo
echo "--- me"; curl -s -o /dev/null -w "%{http_code}\n" -b $J $B/api/c/me
echo "--- logout"; curl -s -b $J -c $J -X POST $B/api/auth/logout; echo
curl -s -o /dev/null -w "after logout %{http_code}\n" -b $J $B/api/c/me
echo "--- login"; curl -s -c $J -X POST $B/api/auth/login -H 'Content-Type: application/json' -d '{"email":"Owner@fictionalclient.example","password":"Harbour-Kai-Test-2026"}'; echo
echo "--- bad code"; curl -s -b $J -X POST $B/api/auth/login/verify -H 'Content-Type: application/json' -d '{"code":"000000"}'; echo
RC=$(python3 -c "import json;print(json.load(open('/tmp/r12_confirm.json'))['recovery_codes'][0])")
echo "--- recovery"; curl -s -b $J -c $J -X POST $B/api/auth/login/verify -H 'Content-Type: application/json' -d "{\"recovery_code\":\"$RC\"}"; echo
curl -s -o /dev/null -w "me %{http_code}\n" -b $J $B/api/c/me
grep -i "set-cookie" -r /dev/null || true
curl -s -D - -o /dev/null -X POST $B/api/auth/login -H 'Content-Type: application/json' -d '{"email":"owner@fictionalclient.example","password":"Harbour-Kai-Test-2026"}' | grep -i set-cookie
echo "--- lockout"; for i in 1 2 3 4 5 6; do curl -s -X POST $B/api/auth/login -H 'Content-Type: application/json' -H 'X-Forwarded-For: 10.9.9.9' -d '{"email":"ae@harbourline.example","password":"wrong-password-xx"}'; echo; done
echo "$SECRET" > /root/r12_kai_secret
