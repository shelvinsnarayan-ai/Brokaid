# brokAId — Known Demo Limitations

This build is the SIRC acceptance demo. It is intentionally scoped down.
When someone in the room asks "is that real?", check this list first.

## External systems (all mocked)

- **CarrierChannel** — quotes and bind confirmations are canned. `MOCKED · carrier` on any surface that touches this.
- **Payment rails** — Stripe / PayTo / card capture never happen. Payment mandate creation, initiation and receipt are all synthesised. `MOCKED · payment`.
- **E-sign** — signature requests and completions are synthesised. No DocuSign or PandaDoc integration. `MOCKED · e-sign`.
- **Email** — outbound emails land on disk at `/tmp/brokaid_emails/*.eml`. No Resend / SendGrid / SES call is made. `MOCKED · email`.
- **Insurer claims channel** — FNOL lodgement and status polling are synthesised. `MOCKED · insurer`.
- **AMS sync** — no external AMS is reachable. Any AMS row is `MOCKED · ams`.
- **Premium funders** — funder integration is deferred; the chase-ladder engine is stubbed. `MOCKED · funder`.
- **Weather / cyber feeds** — signals are synthesised. `MOCKED · signal`.

## Governance-locked scaffolding a technical audience will notice

- **`/dev/login`** is mounted only when `AUTH_DEV_MODE=true`. A visible "DEV AUTH — not for production" banner sits above every route. Vite refuses to build when both `AUTH_DEV_MODE=true` and `NODE_ENV=production`.
- **`POST /api/auth/dev/impersonate`** exists in the demo env only. Same guard as above.
- **Model versions never on screen.** No `claude-...` string is ever rendered. If you find one, that's a bug — flag it, do not paper over it.
- **CI deny-list** blocks internal agent names (`intAIke`, `plAIce`, `clAIms`, `complAInce`, `liAIson`, `brAIn`, `gAIte`, `trAIl`, `retAIn`, `finAInce`, `terrAIn`, `campAIgn`, `docs`, `notify`, `certs`) from appearing in `frontend/dist`. That's why the operator surface calls them by their role, not their codename.

## Repository / build posture

- **GitHub push is waived.** Signed commits accumulate locally under `brokaid-build <build@synaion.com.au>` (ed25519). The verified-build pill on the operator shell displays the short SHA and `G`-status of the currently built commit. Push to `synaion/brokaid` is deferred.
- **PostgreSQL is containerised in-pod**, not managed. Migration to a managed Postgres instance is deferred until post-SIRC hardening.
- **No always-on workers.** Every scheduled job (`compute_kpis`, `timer_sweep`, etc.) is bounded — advisory-locked, max-runtime capped, exits non-zero on error.
- **Nightly chain-verify cron** is designed but not scheduled in this pod. On-demand verification via the export bundle covers the demo.

## Deferred to post-SIRC

- **`finAInce` agent + chase-ladder engine** — currently a placeholder. No autonomous premium funding chase is executed.
- **Real PDF certificate generation** — the client cover page renders the certificate content inline plus a real scannable QR PNG (`GET /api/c/certificate/{id}/qr.png`); a downloadable PDF is a fast follow.
- **Bundle-integrity chip on the operator's machine** — the build pill is a first step; a full-machine attestation (SLSA-style) is deferred.
- **Real carrier / real insurer / real payments** — real integrations are Phase 19+, not part of this seven-week SIRC build.

## What is NOT mocked (in case someone asks)

- **The ledger** (Postgres append-only `events` table, SHA-256 hash-chain per tenant, RLS, BEFORE UPDATE trigger denying mutation) is real.
- **The offline verifier** (`packages/kernel/verifier/verify.js`, 130 lines) is real — bundle it, run it on any Node 18+ laptop.
- **RBAC** on FastAPI routers (operator MFA gate + role capability check) is real.
- **The build-time deny-list scan** on `frontend/dist` is real and blocks merges.
- **Signed commits** under `brokaid-build` are real ed25519 signatures.
