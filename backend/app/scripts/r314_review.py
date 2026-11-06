"""R3.14: rerun the R3.11 four-model review on HEAD with the same models, prompt, A20 criteria, validation and consolidation.
Only the feature summary is updated (R3.12–R3.13c) and the evidence is fresh. The published report adds "vs R3.11" columns.
Usage: python -m app.scripts.r314_review /tmp/r314  (after scripts/review/r314_capture.py)"""
# tenant-safe: system-only (Harbourline resolved by slug; review stored in that tenant)
import asyncio
import sys

from app.scripts import r311_review as base

PREV = "R3.11-20261106-0903-d0bc0d"
base.FEATURES = base.FEATURES.replace("Not in scope of this evidence: anything not shown in the screenshots.", """Added since the R3.11 review (R3.12–R3.13c):
- Client: policy page always has a Renewal section (state + "Review my renewal" / "Tell us about changes before renewal"); Home shows a
  "renews in N days" card; renewal checklist asks to confirm sums insured and key values (changes become broker-reviewed requests).
- Client: Profile → "People on this account": owner invites with presets (Bookkeeper, Co-owner, Read only, Custom), changes rights,
  removes people; 72h single-use invite + authenticator enrolment; "Copy invite link" when email is off.
- Client: Documents library grouped per policy (current schedule, previous schedules, PDS & wording, certificates, receipts & signed
  acceptances, letters); "Ask your broker for a copy" when missing; "?" explainers (bottom sheet on phone, dialog on desktop) for
  insurance terms; "What happens next" panels on change, claim, pay and certificate.
- Broker: plain-sentence timelines (no raw ids/JSON for AE/Principal; Operator-only Details); alerts grouped by client and severity;
  queue rows show client, what's needed, due by, Review, and a More toggle; Governance status cards with collapsible sections;
  staff help tooltips and "How this works" playbooks; phone menu and stacked tables at 390 px on every staff route;
  staff upload of policy schedules / PDS (PDF only, safety-checked, replace/withdraw, client notified); test data hidden from staff views.
Not in scope of this evidence: anything not shown in the screenshots.""")


async def run(d: str) -> None:
    from app.kernel import review_publish
    review_publish.RELEASE, review_publish.PREV_ID, review_publish.FEATURES_NOTE = "R3.14", PREV, base.FEATURES
    await base.main(d)


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1] if len(sys.argv) > 1 else "/tmp/r314"))
