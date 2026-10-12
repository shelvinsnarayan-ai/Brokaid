# RULINGS.md — carries and open items (R0.6)

Lines are added when an R0.6 acceptance item fails or is cut and carries to M2.

| # | Item | Status | Carry / note |
|---|------|--------|--------------|
| 5 | AE rail: Renewals · Inbox | Not built: no routes exist | Carry to M2. The AE rail shows only Today · Clients until these surfaces exist (no empty or placeholder tabs). |
| 5 | Principal "Weekly pack" | Not built | Carry to M2. The Principal group shows Overview · Daily Brief · Shadow Mode. |
| 7 | Things UI | Home renders things; Cover still renders policies | Cover "Things / Policies" toggle and the Thing page carry to M3. |
| 10 | Registry as sole counter | Registry counts exist (`/api/o/registry`, data_basis=registry) and show on Agent operations | Existing Overview KPI tiles still read their own projections. Repointing every dashboard tile to the registry carries to M2. |
| 10 | Legacy tenant | The BurMac tenant (slug `burmac`) is left intact, with its ledger untouched | Its demo logins were repointed to Harbourline; the tenant itself has no users. `phase16_touch.py` (Postgres path) still names it, as its own tenant. |
| 3 | Login emails | Demo logins keep `@burmac.example` addresses | Not shown on any screen (lowercase, credentials only). Renaming the addresses would break the repoint and the scripts; carry the rename to M2. |
| 11 | build-info env | Preview reports `env=development` | `production` only where `ENVIRONMENT=production` is set; that is owner deploy territory (ruling C/D). |
