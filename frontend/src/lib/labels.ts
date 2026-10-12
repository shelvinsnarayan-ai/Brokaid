// Plain-language labels for ledger/queue enums. Display only — ledger names unchanged.
const LABELS: Record<string, string> = {
  endorsement_execute_instructed_quoted: "Endorsement — execute, instructed and quoted",
  submit_to_market: "Submit to market",
  structure_risk: "Structure the risk",
  request_info: "Request for information",
  certificate_issue: "Certificate issue",
  claim_admin: "Claim administration",
  comms_service: "Client communication",
  trust_money: "Trust money",
  AgentOutputProduced: "Agent output produced",
  EndorsementQuoteReady: "Endorsement quote ready",
  ClientChangeRequested: "Client change requested",
  CarrierQuoted: "Carrier quoted",
  SubmissionDrafted: "Submission drafted",
  awaiting_operator_class_shadow_authoritative: "Awaiting broker approval",
  intake_only: "Needs broker review",
  unmatched_policy_field: "Details don't match the policy — review",
  certificate_broker_task: "Certificate — broker to handle",
  CertificateRequestNeedsBroker: "Certificate request needs broker",
  personal_advice_required: "Personal advice required — broker to handle",
  out_of_scope: "Out of scope for the portal",
  AdviceBoundaryClassified: "Advice boundary classified",
  ChangePacketIssued: "Change packet issued",
  RequestNeedsHumanReview: "Request needs broker review",
  EnvelopeAbandoned: "Request abandoned",
  account_executive: "Account executive",
  principal: "Principal",
  hitl_predicate_matched: "Needs a broker's check",
  "hitl_predicate matched": "Needs a broker's check",
  quote_ready_operator_confirmation_required: "Quote ready — broker to confirm",
  premium_shift_gt_10pct: "Premium moves more than 10%",
  sum_insured_delta_gt_25pct: "Sum insured changes more than 25%",
  non_standard_wording: "Non-standard wording",
  ambiguous_reply: "Client's reply is unclear",
  coverage_language_detected: "Mentions what's covered",
  coverage_language_guard: "Mentions what's covered",
  compliance_check_pending: "Compliance check pending",
  new_business: "New business",
  operator_class_shadow_authoritative: "Awaiting broker approval",
  compliance: "Compliance",
};

const humanise = (s: string) => {
  const t = s.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/[_.]+/g, " ").trim().toLowerCase();
  return t.charAt(0).toUpperCase() + t.slice(1);
};

export const confidenceWord = (s: number | null | undefined) =>
  s == null ? "Not scored" : s >= 0.85 ? "High" : s >= 0.6 ? "Medium" : "Low";

export function labelFor(key: string | null | undefined): string {
  if (!key) return "—";
  return LABELS[key] ?? humanise(key);
}
