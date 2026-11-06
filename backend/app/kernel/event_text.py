"""R3.13 — every ledger event as a plain sentence for staff screens. One mapping (event type → template); unmapped types
fall back to "System update recorded". Placeholders: {who} (actor's name) plus payload keys; missing keys render empty."""
from __future__ import annotations

import json
import re
import string

GENERIC = "System update recorded"
# Deliberately generic: engine/plumbing events staff don't act on (raw detail stays behind the Operator's Details toggle).
GENERIC_TYPES = {
    "AdviceBoundaryClassified", "AgentInvocationBlocked", "AgentInvocationBudgetBreached", "AgentInvocationCompleted",
    "AgentInvocationStarted", "ChainVerified", "EmailRenderedDevMode", "InAppNotification", "Notification", "SeedMarker",
    "TenantGenesis", "VerifierTokenIssued", "TenantSwitched", "LoginSucceeded", "PushTestFeedback", "JobDiscarded",
    "DocumentDeliveryMissing", "RecordRedacted", "TenantDataArchived", "SpendAlert", "SubmissionDrafted",
    "AccessDenied", "AdminBundleDownloadRequested", "AgentInvocationRefused", "Citation", "Contents", "EmailSent", "JobRunCompleted",
    "JobRunStarted", "KPIDailyComputed", "MagicLinkConsumed", "MagicLinkIssued", "MfaVerified", "SprintCapAlert", "TimerFired",
    "VerifierRead",
}
GENERIC_PREFIXES = ("Demo", "Sandbox")

T: dict[str, str] = {
    # matters, queue, approvals
    "Approval": "{who} {decision} the {class_label}",
    "OperatorApproval": "{who} approved the {class_label} for the client",
    "AgentOutputProduced": "Assistant drafted a {class_label} for review",
    "QueueCardEnqueued": "{class_label} added to the queue",
    "QueueCardClosedStale": "Queue item closed as out of date",
    "MatterCreated": "New matter opened: {subject}",
    "MatterStatusChanged": "Matter moved to {status}",
    "ComplianceBlockAdded": "Compliance hold added: {reason}",
    "ComplianceBlockCleared": "Compliance hold cleared",
    "RequestNeedsHumanReview": "A request needs a broker to review it",
    "ClientRequestReceived": "Client sent a request: {summary}",
    "ClientChangeRequested": "Client asked for a change to their cover",
    "ClientMessageHITL": "A draft reply to the client is waiting for review",
    "ClientMessageReleased": "{who} sent a reply to the client",
    "ClientQuestionAsked": "Client asked a question",
    "BrokerReplied": "{who} replied to the client",
    "EnvelopeAbandoned": "Request closed without going ahead",
    "EndorsementQuoteReady": "Insurer quote ready for the change",
    "EndorsementExecuted": "Change to cover confirmed with the insurer",
    "CarrierQuoted": "Insurer quoted {amount_text}",
    "ChangePacketIssued": "Change instructions issued to the insurer",
    "SignatureRequested": "Client asked to accept",
    "SignatureCompleted": "Client accepted",
    "OfflineSignatureRecorded": "{who} recorded a signature received offline",
    "ClientAcceptanceRecorded": "Client acceptance recorded",
    "payment.advised": "Client said they've paid",
    "PaymentInitiated": "Payment started",
    "PaymentMandateCreated": "Direct debit set up",
    "PaymentReceived": "Payment received",
    "PremiumReceived": "Premium received {amount_text}",
    "PremiumReceiptReversed": "{who} reversed a premium receipt",
    "TrustPaymentRecorded": "Trust payment recorded",
    "TrustReconciliationSignedOff": "{who} signed off the trust reconciliation",
    "BankStatementImported": "{who} imported a bank statement",
    "PaymentSettingsUpdated": "{who} changed payment settings",
    "PromiseMade": "{who} promised the client: {text}",
    "PromiseKept": "Promise to the client kept",
    # advice
    "GeneralAdviceWarningGiven": "General advice warning shown to the client",
    "PersonalAdviceApprovalRequested": "Personal advice waiting for approval",
    "PersonalAdviceApproved": "{who} approved personal advice",
    "PersonalAdviceRejected": "{who} sent personal advice back",
    "PersonalAdviceRefused": "{who} declined to give personal advice",
    "PersonalAdviceWithdrawn": "Personal advice withdrawn",
    "TmdFitChecked": "Target market fit checked",
    "TmdOutsideApproved": "{who} approved a sale outside the target market",
    "SignificantDealingRecorded": "Significant dealing outside the target market recorded",
    # policies, documents
    "PolicyCreated": "Policy set up: {product}",
    "PolicyImported": "Policy imported: {product}",
    "PolicyUpdated": "Policy details updated",
    "PolicyRenewed": "Policy renewed",
    "PolicyLapsed": "Policy lapsed",
    "PolicyDocumentAdded": "{who} added a {kind_label} for the client",
    "PolicyDocumentWithdrawn": "{who} withdrew a {kind_label}: {reason}",
    "PolicyDocumentReplaced": "{who} replaced a {kind_label}",
    "DocumentDelivered": "{doc_kind} given to the client",
    "DisclosureDocumentUploaded": "{who} uploaded a {kind}",
    "ComparisonDocumentGenerated": "Renewal options comparison prepared",
    "ComparisonEmailResent": "Renewal comparison re-sent to the client",
    "CertificateRegenerated": "Certificate of currency issued",
    "CertificateRequestNeedsBroker": "Certificate request needs a broker",
    "RepairQuoteSummaryUpdated": "{who} corrected the repair quote details",
    "ThingRecorded": "Insured item recorded",
    "CollectionNoticeRecorded": "Privacy collection notice given",
    # claims
    "ClaimFNOL": "Claim reported by the client",
    "ClaimCreated": "Claim opened",
    "ClaimLodged": "Claim lodged",
    "ClaimAcknowledged": "Claim acknowledged",
    "ClaimTriaged": "Claim triaged",
    "ClaimStageChanged": "Claim moved to {stage_label}",
    "ClaimStatusUpdate": "Claim update: {summary}",
    "ClaimInfoRequested": "{who} asked the client for more information",
    "ClaimInfoProvided": "Client sent the information asked for",
    "ClaimDocumentAdded": "Document added to the claim",
    "ClaimNoteAdded": "{who} added a claim note",
    "ClaimPackGenerated": "Claim pack prepared for the insurer",
    "ClaimSubmittedToInsurer": "Claim sent to the insurer",
    "InsurerLodgementRequested": "Claim lodgement requested from the insurer",
    "InsurerLodgementAck": "Insurer confirmed the claim",
    "ClaimAssessmentUpdated": "Assessment updated",
    "ClaimDecisionRecorded": "Insurer decision recorded: {decision}",
    "ClaimDecisionDisputed": "Client disputed the insurer's decision",
    "ClaimSettled": "Claim settled",
    "ClaimTimeframeMet": "Claim handled within the required timeframe",
    "ClaimResolved": "Claim resolved",
    "ClientMessageRewriteRequired": "A draft reply to the client needs rewording before it can go",
    "PlacementRerouted": "Request sent to a different insurer",
    "ClaimFlaggedAsTest": "Claim marked as test data",
    # renewals
    "RenewalStarted": "Renewal started",
    "RenewalStageChanged": "Renewal moved to {stage_label}",
    "RenewalInfoSubmitted": "Client sent their renewal details",
    "RenewalValuesReviewed": "Client reviewed sums insured: {confirmed} confirmed, {requested} change requests",
    "RenewalQuoteRecorded": "{who} recorded a renewal quote",
    "RenewalRecommendationWritten": "{who} wrote the renewal recommendation",
    "RenewalAdviceApproved": "{who} approved the renewal advice",
    "RenewalClientDecision": "Client chose a renewal option",
    "RenewalSigned": "Client accepted the renewal",
    "RenewalBound": "Renewal confirmed with the insurer",
    "RenewalChangePacketIssued": "Renewal change instructions issued",
    "RenewalNudgeSent": "Renewal reminder sent to the client",
    "RenewalLapseConfirmed": "{who} confirmed the policy lapses",
    "RenewalLateBindApproved": "{who} approved a late bind",
    "RenewalRefundChosen": "Refund chosen instead of binding",
    "RenewalRefundOrBindDecision": "{who} decided refund or bind",
    "RenewalsPastDueAfterSuspension": "Renewals overdue after a pause",
    "RenewalDemoReset": GENERIC,
    # clients and people
    "ClientEntityCreated": "Client added",
    "ClientImported": "Client imported",
    "ClientUpdated": "{who} updated client details",
    "ClientTypeSet": "{who} set the client type",
    "ClientAeAssigned": "{who} assigned an account executive",
    "ClientMemberInvited": "Client invited someone to their account",
    "ClientMemberInviteCancelled": "Client cancelled an invite",
    "MemberRemoved": "Someone was removed from the client account",
    "AuthorityChanged": "What someone can do on the account was changed",
    "InviteCreated": "{who} sent an invite",
    "InviteReissued": "{who} re-sent an invite",
    "InviteRevoked": "{who} cancelled an invite",
    "InviteAccepted": "Invite accepted",
    "UserRoleChanged": "{who} changed someone's role",
    "UserDisabled": "{who} turned off someone's access",
    "UserEnabled": "{who} turned someone's access back on",
    "MfaEnrolled": "Authenticator app set up",
    "MfaReset": "{who} reset someone's authenticator app",
    "RecoveryCodeUsed": "A recovery code was used to sign in",
    "LoginFailed": "Sign-in attempt failed",
    "AccountLocked": "Account locked after failed sign-ins",
    "PasswordChanged": "Password changed",
    "PasswordResetRequested": "Password reset requested",
    "PasswordResetRevoked": "Password reset link cancelled",
    "SessionsRevoked": "Signed out on all devices",
    "GoogleLinked": "Google sign-in linked",
    "PushDeviceRemoved": "Alert device removed",
    "PushDeviceRenamed": "Alert device renamed",
    "PushTestSent": "{who} sent a test alert",
    "ComplaintStageChanged": "Complaint moved to {stage_label}",
    "PrivacyRequestStageChanged": "Privacy request moved to {stage_label}",
    "LegalHoldSet": "{who} placed a legal hold",
    # tenant, compliance, governance
    "TenantCreated": "Brokerage set up",
    "BrokerageCreated": "Brokerage set up",
    "LicenseeCreated": "Licensee set up",
    "TenantStatusChanged": "Brokerage status changed",
    "TenantSettingsChanged": "{who} changed brokerage settings",
    "BrandingSettingsChanged": "{who} changed branding",
    "BusinessDetailsUpdated": "{who} updated business details",
    "EmailAllowlistUpdated": "{who} changed the email allowlist",
    "ComplianceDraftCreated": "{who} started a compliance document draft",
    "ComplianceDraftRevised": "{who} revised a compliance draft",
    "ComplianceDocumentApproved": "{who} approved a compliance document",
    "ComplianceDocumentLive": "Compliance document went live",
    "ComplianceDocWithdrawn": "{who} withdrew a compliance document",
    "ComplianceDocArchived": "Compliance document archived",
    "InsurerImported": "Insurer added",
    "ImportCommitted": "{who} finished an import",
    "KillSwitchEngaged": "{who} paused an assistant: {reason}",
    "KillSwitchReleased": "{who} resumed an assistant",
    "BackupRestoreDrill": "Backup restore drill recorded",
    "DrillRecorded": "Drill recorded",
    "ReleaseCheckCompleted": "Release check finished: {result}",
    "ReviewPublished": "UX review published",
    "JobRetried": "{who} retried a background job",
    "DemoCredentialsDisabled": "Demo sign-ins turned off on a public site",
}
STAGE = {"info_requested": "waiting on the client", "client_decision": "client deciding", "quoting": "quoting",
         "triage": "triage", "lodged": "lodged with insurer", "assessing": "assessment", "settled": "settled"}
KIND = {"schedule": "policy schedule", "pds": "PDS", "wording": "policy wording", "certificate": "certificate"}
LINKS = (("claim_id", "/o/claims/{}"), ("envelope_id", "/o/queue/{}"), ("thread_id", "/o/inbox/{}"), ("client_id", "/o/clients/{}"))


class _Safe(dict):
    def __missing__(self, k):
        return ""


def _label(s) -> str:
    return str(s or "").replace("_", " ").replace(".", " ").strip()


def is_generic(t: str) -> bool:
    return t in GENERIC_TYPES or t.startswith(GENERIC_PREFIXES)


def describe(etype: str, payload, who: str | None = None) -> dict:
    p = payload if isinstance(payload, dict) else (json.loads(payload) if isinstance(payload, str) and payload.startswith("{") else {})
    tpl = T.get(etype) or GENERIC
    stage = p.get("to") or p.get("stage") or p.get("status")
    amount = p.get("amount") or p.get("premium") or p.get("total")
    vals = _Safe({k: v for k, v in p.items() if isinstance(v, (str, int, float))} | {
        "who": who or "Someone", "class_label": _label(p.get("class_key")) or "request", "stage_label": STAGE.get(stage, _label(stage)),
        "kind_label": KIND.get(p.get("kind"), _label(p.get("kind")) or "document"), "doc_kind": _label(p.get("kind")) or "Document",
        "amount_text": f"A${float(amount):,.2f}" if isinstance(amount, (int, float)) or (isinstance(amount, str) and re.fullmatch(r"\d+(\.\d+)?", amount)) else "",
        "decision": p.get("decision") or "approved"})
    text = string.Formatter().vformat(tpl, (), vals)
    text = re.sub(r"\s+([:,])\s*$", "", re.sub(r"\s{2,}", " ", text)).strip().rstrip(":").strip()
    text = text[0].upper() + text[1:] if text else GENERIC
    link = next((fmt.format(p[k]) for k, fmt in LINKS if isinstance(p.get(k), str) and p.get(k)), None)
    return {"text": text[:200], "link": link}


async def names(store, tid: str, actors: list) -> dict:
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import use_tenant
    ids = {a.get("id") or a.get("user_id") for a in actors if isinstance(a, dict)} - {None}
    if not ids:
        return {}
    async with use_tenant(tid):
        return {u["user_id"]: (u.get("display_name") or "Someone") async for u in
                wrap(store).users.find({"user_id": {"$in": list(ids)}}, {"user_id": 1, "display_name": 1})}


def who_of(actor, nm: dict) -> str | None:
    a = actor if isinstance(actor, dict) else (json.loads(actor) if isinstance(actor, str) and actor.startswith("{") else {})
    if a.get("kind") in ("agent", "system"):
        return "The assistant" if a.get("kind") == "agent" else "brokAId"
    return nm.get(a.get("id") or a.get("user_id")) or (a.get("name") if a.get("kind") != "human" else None)


async def render(store, tid: str, rows: list[dict]) -> list[dict]:
    nm = await names(store, tid, [r.get("actor") if isinstance(r.get("actor"), dict) else {} for r in rows])
    for r in rows:
        r.update(describe(r.get("type", ""), r.get("payload") or {}, who_of(r.get("actor") or {}, nm)))
    return rows


# ---- R3.13b: server-side redaction for staff who aren't Operator/platform admin ----
RAW_ROLES = {"operator", "platform_admin"}
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HEX = re.compile(r"^[0-9a-f]{40,64}$", re.I)
# Person, credential and engine identifiers never go to AE/Principal. Entity ids the UI routes with (claim_id, policy_id …) stay.
DENY_KEYS = {"user_id", "membership_id", "invite_id", "token_id", "session_id", "event_id", "source_event_id", "proposal_ref",
             "resolved_by_event", "grantee", "idempotency_key", "actor_id", "prev_hash", "hash", "chain_hash",
             "payload", "raw", "registry"}


def raw_ok(session) -> bool:
    return getattr(session, "active_role", None) in RAW_ROLES


def _deny(k: str, v) -> bool:
    kl = k.lower()
    if kl in DENY_KEYS or "sha256" in kl or kl.endswith("_hash"):
        return True
    if isinstance(v, str) and (kl == "by" or kl.endswith("_by")) and _UUID.match(v):
        return True
    return isinstance(v, str) and bool(_HEX.match(v))


ALLOWED_PICKERS = {"aes"}  # AE filter/assign options: {user_id, name} is the value the picker submits


def redact(obj):
    if isinstance(obj, dict):
        return {k: ([{"user_id": a.get("user_id"), "name": a.get("name")} for a in v if isinstance(a, dict)]
                    if k in ALLOWED_PICKERS and isinstance(v, list) else redact(v))
                for k, v in obj.items() if not _deny(str(k), v) and not _id_key(k)}
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def plain(rows: list[dict]) -> list[dict]:
    """Events as staff see them: sentence, time, link, who. No type/payload/ids."""
    return [{"ts": r.get("ts"), "text": r.get("text") or GENERIC, "link": r.get("link"),
             "who": (r.get("actor") or {}).get("name") if isinstance(r.get("actor"), dict) else None} for r in rows]


def staff_out(session, obj):
    return obj if raw_ok(session) else redact(obj)


def _id_key(k) -> bool:
    """Dict keys that are ids (uuid/hex) never reach AE/Principal; callers relabel known per-person maps first."""
    return isinstance(k, str) and bool(_UUID.match(k) or _HEX.match(k))


ROLE_LABEL = {"ae": "Account executive", "principal": "Principal", "operator": "Operator", "accounts": "Accounts",
              "platform_admin": "Platform admin", "client_owner": "Client owner", "claims": "Claims"}


async def person_labels(store, tid: str, roles: dict) -> dict:
    """{user_id: display name}; ids with no user record become '<Role> A', '<Role> B' … (never the raw id)."""
    nm = await names(store, tid, [{"id": u} for u in roles])
    seen: dict[str, int] = {}
    out = {}
    for uid, role in roles.items():
        if uid in nm and nm[uid] != "Someone":
            out[uid] = nm[uid]
            continue
        base = ROLE_LABEL.get(role or "") or ROLE_LABEL.get(str(uid).split("-")[0]) or "Team member"
        seen[base] = seen.get(base, 0) + 1
        out[uid] = f"{base} {chr(64 + seen[base]) if seen[base] <= 26 else seen[base]}"
    return out


async def kpi_people(store, tid: str, kpis: list[dict]) -> list[dict]:
    """KPI metas name people, not ids: events[].user_id -> who, per_user_count keys -> names."""
    roles: dict = {}
    for k in kpis:
        m = k.get("meta") or {}
        for e in m.get("events") or []:
            if isinstance(e, dict) and e.get("user_id"):
                roles.setdefault(e["user_id"], e.get("role"))
        for uid in (m.get("per_user_count") or {}):
            roles.setdefault(uid, None)
    lab = await person_labels(store, tid, roles)
    for k in kpis:
        m = k.get("meta") or {}
        for e in m.get("events") or []:
            if isinstance(e, dict) and e.get("user_id"):
                e["who"] = lab[e["user_id"]]
        if isinstance(m.get("per_user_count"), dict):
            pc: dict = {}
            for uid, n in m["per_user_count"].items():
                pc[lab[uid]] = pc.get(lab[uid], 0) + n
            m["per_user_count"] = pc
    return kpis


_NOTE_FIELDS = ("message", "raw_message", "note", "reason", "term_label", "title", "draft", "body", "summary")
_QA = re.compile(r"TEST_|\bQA\b")  # case-sensitive: "qa-swap" idempotency keys etc. don't count


def is_test_note(e: dict) -> bool:
    """QA/TEST_/cleanup notes and demo test bookkeeping are test artifacts, hidden from AE/Principal timelines."""
    if re.match(r"^Demo.*(Flag|Artifact)", e.get("type") or ""):
        return True
    p = e.get("payload") or {}
    return isinstance(p, dict) and any(isinstance(p.get(k), str) and (_QA.search(p[k]) or "cleanup" in p[k].lower())
                                       for k in _NOTE_FIELDS)
