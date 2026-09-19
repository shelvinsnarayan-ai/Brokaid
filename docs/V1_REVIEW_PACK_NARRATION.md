brokAId V1 MVP — Narration Scripts (§11)
=========================================

Two voice tracks intended to run behind the silent screen recordings
in `docs/review_pack_assets/recordings/`. Both are short enough to
land inside Shelvin's ten-minute target when combined.

**Recording production is out of scope for this pack.** No TTS.
No LLM. These scripts are handed off to whoever records the voice
on Shelvin's side.

Voice discipline
----------------

- **Harbour voice** (client surfaces): calm, plain-English, second-
  person, no jargon. Never uses the word "AI". Never says a model
  name. Attributes actions to "your broker", not "the system".
- **Ledger voice** (operator surfaces): precise, third-person, comfortable
  with `j/k`, "the queue", "the ledger", "trAIl", "gate". Uses
  concrete numbers. Never says a model name.

Both voices are safe with the product name **brokAId** (pronounced
"broke-aid"). Neither says any of the internal agent names
(intAIke, plAIce, clAIms, complAInce, liAIson, brAIn, gAIte, trAIl)
out loud — those are architecture, not product.

---

## Track A — Harbour (client), ~2:30

### Scene 1: Home
> **Hi — this is what your broker looks like when we've done it right.
> One place, one screen, and only what's actually yours to do.**
> **Right now we've got no next action for you, so the panel just
> says so. If your broker needs something, that panel is the first
> thing that changes.**
> *[camera holds on the "No next action" card]*

### Scene 2: Cover
> **Here's every policy in one list. The ones we manage — like this
> Marine Liability — we can quote, endorse, and issue on your
> behalf. The ones we track, like this Cyber policy, we don't
> touch — you asked us to keep an eye on them, so they're here for
> completeness.**
> **The little pill on the card, "Mocked · Carrier" — that's not
> a warning, it's honesty. This is a demo build, and the insurer
> connection is stubbed. Every one of those pills means the same
> thing: this specific edge is not live yet.**

### Scene 3: Money & Claims
> **Money is where invoices and receipts land. Nothing to pay
> today, so the surface says so. In Singapore this would say
> PayNow / GIRO instead of PayTo — the rail label follows the
> jurisdiction, not the app.**
> **Claims — one lodged claim. Tap in to see the status. Any
> written update from the insurer flows through your broker
> first; nothing here talks about your coverage in advice terms
> without a person on the other side.**

### Scene 4: Ask
> **This is your broker line. Type a plain-English question, hit
> send. If it's factual, you'll get a written reply. If it's about
> whether you're covered, we route it to a human — every time.**

### Scene 5: Profile & jurisdiction preview
> **Your account. If you'd like to see how the app would look if
> you were domiciled somewhere else, this jurisdiction-preview
> control renders SG or another region without actually changing
> anything on your file. It's a demo affordance — the banner says
> so.**

### Closing
> **Everything on this side is deliberately quiet. If your broker
> is doing their job, this side of the app should feel calm.**

---

## Track B — Ledger (operator), ~2:30

### Scene 1: Control tower
> **This is the operator's control tower. Twelve KPIs across the
> top, four of them drillable — response time, autonomous
> percentage, approvals load, compliance health. The four with
> the "drill" chip open the underlying events; the other eight
> are read-only for now.**
> **The Daily Brief pane on the left is projection-only — no
> narrative, no advice. Six numbers, three panels. The queue
> teaser on the right is your five most-urgent HITL items. All
> five are past their SLA right now — that's the seed state
> intentionally driving today's demo.**

### Scene 2: Queue
> **The full queue. Filter with slash. Move with `j` and `k`.
> Open with Enter. Approve with `a`, reject with `r`. Every row
> shows SLA, class, client, gate reason, confidence, countdown,
> and the operator route.**

### Scene 3: Decision card
> **A decision card. Three columns. On the left, the proposal
> and the four actions — approve, edit, reject, block. On the
> right, the confidence bar split into schema, completeness,
> citations, and class. The scalar you saw in the queue is the
> minimum of those four components — it can't dominate over
> the weakest one.**
> **The gate reason is written in English: non_standard_wording.
> That's not a categorical failure — that's brokAId saying "I
> want a human to sign off before this leaves the building."**

### Scene 4: Compliance, block-and-clear
> **Block-and-clear. If a proposal shouldn't move, block it with
> a reason. That reason goes into the ledger. Clearing takes a
> second reason and reverses the block. Both events are provable
> — no one can hide a stop-and-go inside a matter.**

### Scene 5: Daily Brief, Client 360, Shadow Mode
> **The full Daily Brief — same six tiles, no prose. Client 360 is
> minimal in this build: industry, domicile, active policies,
> open matters, and the last seven ledger events. Shadow Mode is
> where agents earn their write access. Every class is locked
> today — because no class has cleared ninety-five percent
> agreement with zero E&O misses. That's the whole point of the
> surface.**

### Scene 6: trAIl export
> **The ledger export. Pick a range or a client, preview the
> event count, download the zip. The zip contains the events, a
> verify.js script, and the SHA of that script. Run it on any
> Node 18 machine — no server needed. If the chain is intact,
> it prints OK. If a byte flipped, it prints FAIL and exits one.**
> **That's the audit story: you don't have to trust our platform
> to trust the trail.**

### Closing
> **The whole system is designed so that whoever's asking —
> your board, a regulator, a client's counsel — the answer is the
> same file, and the file explains itself.**

---

## Prosody notes for the recorder

- Read at 145–160 wpm. This copy has been paced so a natural
  reading against silent .webm captures aligns cuts to scene
  boundaries without editing.
- Never emphasise "MOCKED"; the pill on-screen is doing that
  work. Emphasise "your broker", "the queue", "the ledger",
  and any number.
- Do not read data-testids, class names, or route paths aloud.
