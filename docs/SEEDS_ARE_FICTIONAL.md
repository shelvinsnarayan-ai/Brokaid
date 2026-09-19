# Seeds are fictional

**No real client data of any kind, ever.**

Every seed record added to this repository must be entirely fictitious.
That includes:

- Broker, sub-broker, and staff names
- Insured entity names (individuals and companies)
- Addresses, phone numbers, emails
- Policy numbers, claim numbers, invoice numbers
- Dollar amounts and dates that could correlate to a real case

If a seed dataset appears to resemble a real event, replace it. If in
doubt, replace it.

Any PR that adds seed data must reference this document and confirm
that the reviewer has spot-checked at least three records.

## Phase 4 seeded identities

All identities below are entirely fictional. Every operator user has
the same dev password `dev-password-not-for-production` which is
inert outside `AUTH_DEV_MODE=true`. Client users authenticate via
magic-link only.

### Tenant

- **Slug:** `burmac`
- **Name:** BurMac Insurance Brokers *(fictional)*
- **Brokerage slug:** `burmac-brokerage`
- **Licensee:** BurMac Financial Services Pty Ltd (ACN 000-000-000) *(fictional)*

### Operators (kind = `operator`, brokerage-scoped role)

| Email                            | Display name       | Role         | Can log in? |
|----------------------------------|--------------------|--------------|-------------|
| principal@burmac.example         | Ada Principal      | principal    | yes         |
| deputy@burmac.example            | Ben Deputy         | deputy       | yes         |
| ae@burmac.example                | Cass Executive     | ae           | yes         |
| accounts-a@burmac.example        | Dee Accounts       | accounts     | yes         |
| accounts-b@burmac.example        | Ev Accounts        | accounts     | yes         |
| compliance@burmac.example        | Fin Compliance     | compliance   | yes         |
| auditor@burmac.example           | Gil Auditor        | auditor      | **no** (data-only) |

The two `accounts` users exist so the trust-money two-person rule
is testable with distinct signers.

### Clients (kind = `client`, tenant-scoped link)

| Email                                      | Display name    | Link role       |
|--------------------------------------------|-----------------|-----------------|
| owner@fictionalclient.example              | Kai Owner       | client_owner    |
| finance@fictionalclient.example            | Sam Finance     | client_finance  |

Both are linked to the fictional client entity
`00000000-0000-4000-8000-000000000001` under the BurMac tenant.

## Ops tenant

Phase 3 also auto-creates a `brokaid-ops` tenant for JobRun\* events
emitted by the bounded-job runner. It has no operators or clients.

## Phase 5 seeded entities

### Licensee
- **Name:** Fictional Licensee Pty Ltd *(fictional)*
- **ABN placeholder:** `FICTIONAL_LICENSEE_ABN_0000` *(non-real; string
  literally contains the word FICTIONAL)*

### Client entity
- **Name:** Harbour Marine Services Pty Ltd *(fictional)*
- **`client_id`:** `00000000-0000-4000-8000-000000000001`
- **Industry:** marine_services
- **Domicile:** Sydney, AU
- **Jurisdiction:** AU

Linked to the two phase-4 client users (Kai Owner, Sam Finance).

### Policies (all mocked-insurer)

| policy_id                                    | status  | product          | insurer              | limits (AUD)     | premium (AUD) | source                |
|----------------------------------------------|---------|------------------|----------------------|------------------|---------------|-----------------------|
| 00000000-0000-4000-8000-000000000101         | managed | Marine Liability | Fictional Carrier A  | $10M / $20M agg  | $8,500        | in-house              |
| 00000000-0000-4000-8000-000000000102         | tracked | Cyber            | Fictional Carrier B  | $2M              | $2,200        | manual_entry_burmac   |

### Jurisdiction packs
Two packs (`AU`, `SG`) live in
`backend/app/kernel/jurisdiction.py` and mirror to JSON at
`packages/kernel/jurisdiction/{AU,SG}.json`. **Every** disclosure
string in every pack is prefixed with the literal token
`<FICTIONAL_PLACEHOLDER>`; a test asserts this invariant so no draft
regulatory text can accidentally ship without the marker.
