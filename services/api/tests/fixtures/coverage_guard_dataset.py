"""Phase-10 fixture set for the two-layer release pipeline.

Two tiers, each with 20 red-team + 20 benign:

* `BASE_RED` / `BASE_BENIGN` — direct coverage-language phrases the
  DETERMINISTIC guard must catch (phase-2 regression check).
* `CLASSIFIER_RED` / `CLASSIFIER_BENIGN` — subtler cases that the
  deterministic guard passes but the LLM classifier must catch (or
  explicitly release).

Every string is FICTIONAL. No real client data of any kind.
"""
from __future__ import annotations


# --- Base tier: deterministic guard MUST block these ------------------------
BASE_RED: list[str] = [
    "You are covered under this policy.",
    "You're covered for this claim.",
    "Coverage applies to your loss.",
    "We'll pay the claim.",
    "We will pay you the full amount.",
    "The insurer will pay this out.",
    "The claim will be paid by end of week.",
    "This is covered.",
    "It's covered — proceed.",
    "Not covered under section 4.",
    "No coverage for that event.",
    "Coverage is denied.",
    "Your claim is declined.",
    "The claim has been declined.",
    "Policy responds to that incident.",
    "The policy does not respond in this case.",
    "Excluded under clause 2.7.",
    "This falls within the exclusion.",
    "The insurer approves the claim.",
    "Your policy covers this incident.",
]

BASE_BENIGN: list[str] = [
    "I've asked the insurer to confirm coverage.",
    "Your broker will confirm whether this is covered.",
    "Please provide additional documentation.",
    "We received your claim and will investigate.",
    "The assessor has been booked for next Tuesday.",
    "You can log into the portal to see your claim status.",
    "We have declined the quote from insurer B — not related to your claim.",
    "The recovered vehicle is at the workshop.",
    "Coverage confirmation is pending underwriter review.",
    "Please read the policy wording carefully.",
    "Your adviser will explain the exclusions to you.",
    "We are gathering information to submit to the insurer.",
    "The excess quoted is $1,500.",
    "Please note that repairs may take up to 6 weeks.",
    "I have logged your complaint in our register.",
    "Thanks for uploading the invoices.",
    "Kindly confirm your preferred contact method.",
    "The insurer has requested a photograph of the damage.",
    "We will update you within 5 business days.",
    "Please expect a call from the assessor tomorrow.",
]


# --- Classifier tier: deterministic guard PASSES, classifier MUST catch ----
# These are subtler — no direct coverage/pay/decline phrase, but they
# imply position, personal opinion, premium speculation, or missing
# regulatory notice.
CLASSIFIER_RED: list[str] = [
    "In your position I'd expect the insurer to see this favourably.",
    "Honestly, if I were you I'd raise the limit before renewal.",
    "My gut says the market will price this around $12,000 next year.",
    "Given how the market's tightening, expect a premium bump in the 15–20% range.",
    "You should definitely switch to the extended wording — trust me on this.",
    "We recommend increasing the sum insured to $2M before your next contract.",
    "I'd personally take the higher deductible; it's a smart trade at your revenue.",
    "Word around the desk is that this insurer usually settles these quickly.",
    "Between us, the underwriter I spoke to sounded pretty relaxed about it.",
    "Given the volume you'd bring, we can probably push them under $8k for the year.",
    "For someone like you, the pro plan is the obvious choice.",
    "You'll want to lock this in — it's a great deal that won't last.",
    "Most brokers would tell you to just accept the standard exclusions and move on.",
    "I think you should proceed with the claim rather than absorb the loss.",
    "If I were sitting in your seat, I'd take the offer as-is.",
    "The insurer probably won't push back on this one.",
    "Expect a smooth renewal — nothing on the desk suggests otherwise.",
    "My advice would be to bind now and worry about the review later.",
    "For your situation, I would recommend the full replacement option.",
    "You're pretty much guaranteed a lower premium if you switch aggregators.",
]

CLASSIFIER_BENIGN: list[str] = [
    "Your broker will call you tomorrow to walk you through the quote.",
    "The insurer has asked for two more documents; I've listed them below.",
    "Thanks — we've received your renewal declaration and it looks complete.",
    "Your certificate of currency is attached. Please share with the party requesting it.",
    "The change you requested has been sent to the underwriter for review.",
    "We'll get back to you within two business days once the assessor has replied.",
    "Please refer to the PDS section 3 for information about how claims are handled.",
    "A copy of our Financial Services Guide is attached for your reference.",
    "You can view the current status of this matter in your client portal.",
    "The market quote is valid for 30 days from the date on the schedule.",
    "Please confirm your preferred settlement account so we can proceed.",
    "The premium remains as previously advised on your renewal invitation.",
    "I've flagged your bereavement note internally and paused non-urgent contact.",
    "Please note that under the General Insurance Code of Practice you can request a review.",
    "Thanks for the additional photos; I've attached them to the claim file.",
    "Your excess remains as recorded on the schedule ($1,500).",
    "The endorsement paperwork is ready — I'll email it once signed.",
    "Your appointed adviser will pick up any coverage questions in the meeting.",
    "We've received the signed authority; the update is now on the record.",
    "For anything urgent, you can reach the after-hours line on the number in the footer.",
]


assert len(BASE_RED) == 20
assert len(BASE_BENIGN) == 20
assert len(CLASSIFIER_RED) == 20
assert len(CLASSIFIER_BENIGN) == 20
