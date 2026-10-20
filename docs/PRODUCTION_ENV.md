# Production environment (R3.0)

Paste this into the **production** deployment's environment, then redeploy. Values in `<…>` are yours to fill in.
Never reuse the preview VAPID or backup keys in production; generate fresh ones (commands below).

```
ENVIRONMENT=production
AUTH_DEV_MODE=false
APP_BASE_URL=https://<your-production-host>
CORS_ORIGINS=https://<your-production-host>
MONGO_URL=<set by the platform — do not change>
DB_NAME=<set by the platform — do not change>
BOOTSTRAP_OPERATOR_EMAIL=shelvin.s.narayan@gmail.com
BOOTSTRAP_TENANT_SLUG=burmac,harbourline
PLATFORM_ADMIN_EMAILS=shelvin.s.narayan@gmail.com
EMAIL_AUTH_ENABLED=true
EMAIL_ENABLED=false
EMAIL_ALLOWLIST=shelvin.s.narayan@gmail.com
EMAIL_FROM_NAME=brokAId
EMAIL_REPLY_TO=shelvin.s.narayan@gmail.com
OWNER_ALERT_EMAILS=shelvin.s.narayan@gmail.com
VAPID_PUBLIC_KEY=<new public key>
VAPID_PRIVATE_KEY=<new private key>
VAPID_SUBJECT=mailto:shelvin.s.narayan@gmail.com
BACKUP_ENCRYPTION_KEY=<new Fernet key>
EMERGENT_LLM_KEY=<unchanged — the Universal Key>
EMERGENT_EMAIL_KEY=<unchanged — the email proxy key, if set on preview>
```

Leave these **unset** in production: `QA_PASSWORD`, `QA_TOTP_SECRET`, `RATE_LIMIT_DISABLED`, `BROKAID_ACCEPTANCE_MODE`,
`BROKAID_SEED_CI`, `BROKAID_EMAIL_DEV_DIR`, `CLEAN_START_APPROVED_SLUGS` (set it only for a deliberate clean start).

Notes
- `ENVIRONMENT=production` + `AUTH_DEV_MODE=false` turn off the demo sign-in box, `/demo`, `/dev/login` and impersonation (the
  frontend build compiles the box out when ENVIRONMENT is production). If either is wrong, staff now see a red
  "DEMO MODE ON A NON-PREVIEW HOST" banner and the Release check FAILS (`security.demo_host`).
- `APP_BASE_URL` must be the https address people use; it is used in invite/reset links and makes the session cookie `Secure`.
- `CORS_ORIGINS` = the same origin (comma-separate if you add a second domain). Never `*` in production.
- `EMAIL_AUTH_ENABLED=true` sends invite / reset / MFA-reset emails. `EMAIL_ENABLED=false` keeps alert and document emails
  (incl. the renewal comparison email) off until you turn them on.
- `EMAIL_ALLOWLIST` is the global allow-list. Each brokerage may also have its own list (Brokerage settings → Email); if it
  does, the bootstrap adds `shelvin.s.narayan@gmail.com` to the BurMac and Harbourline lists automatically (ledgered
  `EmailAllowlistUpdated`). Add more addresses (comma-separated) before inviting real staff and clients.
- Generate keys once, store them in your password manager:
  - VAPID: `cd backend && python -c "from py_vapid import Vapid01 as V; v=V(); v.generate_keys(); import base64; from cryptography.hazmat.primitives import serialization as s; print('VAPID_PUBLIC_KEY='+base64.urlsafe_b64encode(v.public_key.public_bytes(s.Encoding.X962, s.PublicFormat.UncompressedPoint)).decode().rstrip('=')); print('VAPID_PRIVATE_KEY='+base64.urlsafe_b64encode(v.private_key.private_numbers().private_value.to_bytes(32,'big')).decode().rstrip('='))"`
  - Backup: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` — losing it means
    backups can't be restored.

## What happens after the redeploy
1. On start-up the bootstrap sends an **Operator invite** to shelvin.s.narayan@gmail.com for BurMac and for Harbourline (two
   emails). It is idempotent: it skips a brokerage where that email already has an account or an open invite, and it no longer
   skips just because another Operator (e.g. the old demo Operator) exists.
2. Open the invite → **set a password** → **scan the QR code with an authenticator app** (or type the key) → save the recovery
   codes.
3. Sign in at `/login` → enter the 6-digit code → **choose the brokerage** (BurMac or Harbourline). Switch later from the rail.
4. Platform admin: because the same email is in `PLATFORM_ADMIN_EMAILS`, the platform console is available after sign-in.
5. To recreate the demo logins in **Harbourline**: Operator → Invites → **Issue demo persona invites** → copy each link and open
   it in a private window (Client, Client bookkeeper, Account executive, Principal). Earlier open links stop working.
6. Run the **Release check** (Governance → Release check). Expect `security.demo_host` and `security.dev_routes` PASS.
7. Real BurMac data is entered in production only, by the BurMac Principal/Operator (Compliance → Business details, imports).
