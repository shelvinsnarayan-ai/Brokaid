# brokAId — Governance (ground rules)

These rules apply to **every phase**, not just phase 0. The repo carries
them so contributors do not need to re-read the brief. Violations are
build failures, not style preferences.

1. **Store: PostgreSQL, not Mongo.** The ledger's integrity guarantees
   (append-only, RLS, triggers denying UPDATE/DELETE, hash chain per
   tenant) require Postgres. If Postgres is not natively available in a
   build environment, stand it up in-container with a persistent volume
   and report back before proceeding. Do NOT fall back to Mongo silently
   — halt and ask.

2. **Repo:** `https://github.com/Synaion/brokaid.git` on `main`.
   Credential: env var `SYNAION_GITHUB_PAT`. Never commit the PAT.
   Assume the PAT rotates; always look it up from the environment.

3. **Commit identity:** `brokaid-build <build@synaion.com.au>`,
   SSH-signed with an ed25519 key. `commit.gpgsign true`,
   `gpg.format ssh`, `user.signingkey` points at the public key file.

4. **Push cadence:** at end of every phase and every working session.
   No code lives only in `/app`.

5. **MOCKED discipline:** any surface that calls a mocked external
   system must render a `MockedBadge` on screen, and the corresponding
   ledger event must carry `mocked: true`. Applies to CarrierChannel,
   payment rails, e-sign, insurer claims channels, AMS, premium
   funders, weather/cyber feeds — everything external is mocked in
   this build.

6. **CI deny-list:** internal agent names must never appear in rendered
   client- or operator-facing bundles. Closed set:
   `intAIke, plAIce, clAIms, complAInce, liAIson, brAIn, gAIte, trAIl,
    retAIn, finAInce, terrAIn, campAIgn, docs, notify, certs`.
   Also block the case-sensitive regex `/[a-z]+AI[a-z]+/` in the compiled
   `apps/web` client and operator bundles. Interpreting the flag as
   case-insensitive would collide with common English tokens (`waiting`,
   `details`, `again`) and with the product name itself; the intent
   demonstrated by every closed-set entry is a literal camel-case `AI`
   between lower-case letters, so the check is case-sensitive.
   **Allow-list:** the brand tokens `brokAId` and `brokAId-*` (any suffix
   variant of the product name). Strings inside routes guarded by
   `AUTH_DEV_MODE` are also allow-listed by tagging their compiled
   chunk with the marker `/*! brokaid:dev-only */`.
   CI job must fail the build on any other match.

7. **Model versions never on screen.** No `claude-…` or model
   identifiers rendered anywhere client- or operator-visible.

8. **Seeds are fictional.** No real client data of any kind, ever.
   Any seed data added later must include a `SEEDS_ARE_FICTIONAL.md`
   note.

9. **No always-on workers.** Every scheduled job is bounded
   (start → do work → exit) with an advisory lock, max runtime, and
   non-zero exit on error.

10. **No data path to underAIter.** Ever. Not even a shared package.

11. **Emergent credit cap for phase 0:** 1000 credits. Alerts at
    500 / 800 / 1000. Hard stop at 1000.

12. **LLM budget for phase 0:** zero. No model calls in this phase.
    If any code path invokes an LLM, that's a bug.
