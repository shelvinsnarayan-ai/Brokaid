# brokAId — Pilot go-live runbook (owner)

Status of the build: **Built, unverified.** Nothing in this document has been run against production. Every step below is for the owner to perform and verify. brokAId keeps the records and runs the workflows that support compliance. It does not make a brokerage compliant.

## 1. Environment variables (production backend)

Set these in the deployment's secret store. Never commit them. "Req" means the app fails fast or a release-check step fails without it.

| Variable | Req | Phase | Purpose / value guidance |
|---|---|---|---|
| MONGO_URL | Req | base | Production Mongo connection string |
| DB_NAME | Req | base | Production database name (unique; never shared with preview) |
| APP_BASE_URL | Req | R1.2 | `https://<prod host>`. Must be https: it drives the `__Host-brokaid_session` cookie and the CSRF origin check |
| CORS_ORIGINS | Req | base | Exactly the production frontend origin(s), comma-separated. `*` is never honoured, and the app refuses to boot in production if it's present |
| ENVIRONMENT | Req | R1.1 | `production`. Fails closed: if it's unset or unrecognised, the app treats it as production. Dev mode needs `development`/`preview`/`test` **and** AUTH_DEV_MODE=true |
| AUTH_DEV_MODE | Req | R1.2 | `false` (removes the red DEV AUTH banner and dev shortcuts) |
| EMAIL_AUTH_ENABLED | Req | R1.2 | `true` |
| BOOTSTRAP_OPERATOR_EMAIL / BOOTSTRAP_TENANT_SLUG | first boot | R1.2 | BurMac's first Operator email and `burmac`. Can be removed after the invite has been accepted |
| CLEAN_START_APPROVED_SLUGS | opt | R1.4 | Leave empty in production unless a clean start has been approved in writing |
| EMERGENT_LLM_KEY | Req | R1.3 | Universal key for AI features. `/api/ready` fails without it |
| SPEND_RATES_JSON | opt | R1.7 | Overrides the AI cost table |
| SPEND_CAP_USD | Req | R1.9 | Monthly AI spend cap; an owner alert fires at 80% |
| SPRINT_CAP_TOKENS | opt | R1.3 | Token cap for the build sprint (leave the default) |
| EMAIL_ENABLED | Req | R1.4 | `true` |
| EMERGENT_EMAIL_KEY or RESEND_API_KEY | Req | R1.4 | Transactional email |
| EMAIL_FROM_NAME / EMAIL_REPLY_TO | Req | R1.4 | Platform defaults. Each tenant can override them in the wizard |
| EMAIL_ALLOWLIST | pilot | R1.4 | During the pilot, limits outbound email to approved addresses/domains. Must include BurMac staff, pilot clients and the owner |
| BROKAID_EMAIL_DEV_DIR | no | dev | Do not set in production |
| VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_SUBJECT | Req | R1.5 | Web push. Generate a fresh production pair |
| BROKAID_LEDGER | Req | R1.1 | Ledger mode (keep the default used in preview unless instructed otherwise) |
| BROKAID_BUILD_SHA / BROKAID_BUILD_BRANCH / BROKAID_BUILT_AT | Req | R1.7 | Stamped by the build. The release check reports the SHA |
| BROKAID_ACCEPTANCE_MODE / BROKAID_SEED_CI | no | test | Must be unset in production (seeds test tenants) |
| BROKAID_INVOKE_MAX_ATTEMPTS, JOBS_WORKERS, JOBS_LEASE_S, JOBS_BACKOFF_BASE_S | opt | R1.1 | Job runner tuning. Defaults are fine |
| INTEGRATION_PROXY_URL | platform | base | Provided by Emergent (object storage / integrations) |
| OBJECT_STORE_LOCAL_DIR | no | dev | Do not set in production |
| BROKAID_DOMAIN | opt | R1.4 | Public domain used in links |
| PLATFORM_ADMIN_EMAILS | Req | R1.9 | Comma-separated owner email(s). The only way to become platform_admin; remove an email to revoke immediately |
| OWNER_ALERT_EMAILS | Req | R1.9 | Where ops alerts go. Must also pass EMAIL_ALLOWLIST |
| BACKUP_ENCRYPTION_KEY | Req | R1.9 | 32 random bytes, base64 (`python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"`). **Store a copy offline.** Without it, backups can't be restored |
| RATE_LIMIT_PER_MIN / RATE_LIMIT_AUTH_PER_MIN | opt | R1.9 | Defaults 1200 / 30 per IP per minute |
| RATE_LIMIT_DISABLED | no | test | Must be unset in production |
| OPS_WATCH_SECONDS / OPS_ERROR_SPIKE_15M / OPS_REQUEST_LOG | opt | R1.9 | Alert watcher interval, error-spike threshold, request-log on/off |
| QA_PASSWORD / QA_TOTP_SECRET | no | preview | Preview QA scripts only. Never in production |

Frontend build: `VITE_API_BASE` / `REACT_APP_BACKEND_URL` = the production backend URL. `BROKAID_AUTH_DEV_MODE=false`. `BROKAID_BUILD_SHA` and `BROKAID_BUILD_SIGNED` are stamped by the build. The production static host must send the headers in `frontend/vite.config.ts` (`SECURITY_HEADERS` + the strict CSP, with no `unsafe-inline` for scripts). If the host can't set headers, record that as an audit item.

## 2. Deployment order
1. Freeze: confirm `scripts/test_all.sh` is GREEN on the signed commit to be deployed, and record the SHA.
2. Provision production Mongo (new database). Confirm the provider's own point-in-time/snapshot backup policy and note it here: ______ (brokAId's backups are per-tenant logical backups, in addition to the provider's; the platform-level Mongo backup scope is **unknown** until you confirm it).
3. Set all the backend env vars (section 1). Generate BACKUP_ENCRYPTION_KEY and VAPID keys fresh and store the backup key offline.
4. Deploy the backend. Check `GET /api/health` → 200, then `GET /api/ready` → 200 with all four checks ok (mongo, object_storage, job_leader, llm_key). Allow up to 60s for the job leader.
5. Build and deploy the frontend with the production API URL. Check that the response headers include CSP, HSTS, X-Frame-Options, Referrer-Policy and Permissions-Policy.
6. Do not run any seed/QA script (`r12_qa_accounts`, `r19_platform_admin`, CI seeds) in production.

## 3. Bootstrap
1. On first boot the platform tenant is created and an invite is sent to each PLATFORM_ADMIN_EMAILS address. Accept it, set a password, and enrol TOTP. Sign-in lands on `/platform`.
2. In `/platform` → Ops, confirm Ready is all ok, the job queue has 0 dead jobs, and AI spend shows the cap.
3. Click **Back up all tenants now**, then **Restore drill** on the platform tenant. It must show PASS.
4. Check that an owner alert email arrives: use **Force a test error** once and confirm one email to OWNER_ALERT_EMAILS. A second forced error within 6h must not send another.

## 4. BurMac setup checklist
In `/platform` → Brokerages → New brokerage:
- [ ] Slug `burmac`, legal name, ABN (the checksum must pass), AFSL or AR number, holiday state, timezone
- [ ] Email sender name and reply-to
- [ ] First Operator email (BurMac's nominated Operator). Test tenant: **unchecked**
- [ ] Confirm the tenant shows status `setup` and quoting mode `manual_only` (mocked insurer paths are off for real tenants)

Then the BurMac Operator signs in and completes Governance → Go-live checklist:
- [ ] Principal assigned
- [ ] FSG uploaded and published (Compliance documents)
- [ ] General Advice Warning text
- [ ] AFCA / complaints text
- [ ] Privacy policy and collection notice
- [ ] Payment settings (trust account details)
- [ ] Email branding
- [ ] iAdviser profile
- [ ] Every AE invited, and every client assigned to an AE
- [ ] Holiday state confirmed

When the checklist is complete, the platform admin clicks **Go live** (it's refused with the missing list until then). The owner should independently review the published FSG/GAW/AFCA/privacy text. brokAId doesn't check its legal adequacy.

## 5. Release-check verification
Run against production with a BurMac Operator session (cookie `__Host-brokaid_session`):
```
bash scripts/release_check.sh https://<prod host> <session id>
```
Required: `RESULT PASS`, the SHA matches the deployed commit, and every step passes, including the R1.9 steps: `/api/ready`, backup under 26h old, last restore drill PASS, and security headers. Steps that create synthetic data are confined to test tenants and quoting mode is respected. Record the output and mark the release **verified** only after you have checked it yourself.

Ongoing: the nightly backup runs automatically, and a weekly restore drill runs on a scratch database. Ops alerts cover dead jobs over 1h, error spikes, release-check FAIL, AI spend over 80% and a missing scheduler leader.

## 6. Rollback
- **Application rollback:** redeploy the previous signed SHA (backend, then frontend). The schema is additive, so no down-migration is needed. Rerun `/api/ready` and the release check.
- **Disable a tenant immediately:** `/platform` → Suspend. Sign-in is refused and existing sessions end. Lift the suspension when resolved.
- **Disable AI:** use the AI kill switch in Governance (per tenant), or remove EMERGENT_LLM_KEY (`/api/ready` will then report llm_key failing).
- **Data restore:** restores only ever go into a new scratch database (`brokaid_restore_*`); the live database is never overwritten by the app. To recover data, run a drill/restore for the tenant, inspect the scratch database, and copy back specific records manually under change control. For full-database recovery, use the Mongo provider's snapshot (section 2, step 2).
- **Revoke platform admin:** remove the email from PLATFORM_ADMIN_EMAILS and restart. It takes effect on the next request.

## 7. Security audit fixes (SEC-001/002 + hardening)
- Dev impersonation, legacy sign-in routes and dev seeds work only in explicit dev mode, and only for demo/test tenants (Harbourline, import-test, ci-*). They're refused (404) for BurMac, wizard-created brokerages, the platform tenant and platform admins.
- Boot guard: in dev mode, the app refuses to start if any real tenant is live or holds imported data. iAdviser imports into a real tenant run only with ENVIRONMENT=production.
- The release check FAILs if dev or legacy sign-in routes appear in the production API.
- CSV exports neutralise formula cells. JSON bodies with `$` keys are rejected (400). XLSX imports are capped by expanded size, sheets and rows.
- uvicorn's raw access log is disabled; it was printing invite tokens. **Before go-live:** treat the existing preview supervisor logs as containing preview invite tokens, and expire any open preview invites.

## 8. Known limits going into the audit
- Object storage has no delete API, so backups past retention (30 daily / 12 monthly) are marked expired in the catalogue but stay stored.
- `emergentintegrations` pins `openai==1.99.9`; the environment runs openai 2.x (needed by the patched litellm). pip reports the conflict; the live LLM eval passes on it.
- Low/moderate frontend audit findings remain (0 high/critical). See `scripts/dep_audit.sh` output.
- In the dev server, the CSP allows inline scripts (needed for Vite HMR); the production preview headers do not.
- Eval case `rem-04` is a known safe-direction flake (it doesn't perform the vehicle removal) and is tracked in the eval history.
