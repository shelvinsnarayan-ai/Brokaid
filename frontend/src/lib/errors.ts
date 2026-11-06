/** R3.15: one place that turns API error codes into plain words + what to do next (both apps). No imports: node-testable. */
export type Plain = { title: string; next: string };

export const ERROR_MESSAGES: Record<string, Plain> = {
  not_found: { title: "We couldn't find that.", next: "It may have been moved or closed. Go back and open it again from the list." },
  claim_not_found: { title: "We couldn't find that claim.", next: "Open it again from Claims, or message your broker." },
  policy_not_found: { title: "We couldn't find that policy.", next: "Open it again from Cover, or message your broker." },
  thread_not_found: { title: "That conversation isn't available.", next: "Open it again from Messages." },
  file_not_found: { title: "That file isn't available any more.", next: "Ask for it to be sent again." },
  renewal_not_found: { title: "We couldn't find that renewal.", next: "Open it again from the policy page." },
  envelope_not_found: { title: "That request isn't available.", next: "Go back to the queue and open it again." },
  approval_not_found: { title: "That approval isn't available.", next: "Refresh the thread; it may already be decided." },
  approval_not_pending: { title: "That approval has already been decided.", next: "Refresh the thread to see the result." },
  already_resolved: { title: "Someone has already dealt with this.", next: "Refresh to see what was done." },
  forbidden: { title: "You don't have access to this.", next: "Ask the person named below to give you access." },
  staff_only: { title: "This is for broker staff.", next: "Go back to your home page." },
  operator_only: { title: "Only the Operator can do this.", next: "Ask the Operator or your Principal." },
  operator_or_principal_only: { title: "Only the Operator or Principal can do this.", next: "Ask your Principal." },
  platform_admin_only: { title: "Only a platform admin can do this.", next: "Ask the Operator." },
  operator_role_required: { title: "This needs a broker staff login.", next: "Sign in with your staff account." },
  client_role_required: { title: "This needs a client login.", next: "Sign in with your client account." },
  client_only: { title: "This is for clients.", next: "Go back to your home page." },
  authority_required: { title: "Your access on this account doesn't include this.", next: "Ask the account owner to add it." },
  claim_closed: { title: "This claim is closed.", next: "Message your broker if you need to reopen it." },
  unsafe: { title: "That file can't be accepted.", next: "Save it as a plain PDF or photo and try again." },
  attachment_invalid: { title: "That file can't be attached.", next: "Use a PDF or photo under 10 MB." },
  file_too_large: { title: "That file is too big.", next: "Use a file under the size limit, or send it in parts." },
  rate_limited: { title: "Too many tries in a row.", next: "Wait a minute, then try again." },
  invalid_credentials: { title: "That email or password didn't match.", next: "Check them and try again, or reset your password." },
  mfa_required: { title: "Please confirm it's you.", next: "Enter the code from your authenticator app." },
  conflict: { title: "This changed while you were looking at it.", next: "Refresh to see the latest, then try again." },
  validation: { title: "Some details need fixing.", next: "Check the highlighted fields below." },
  network: { title: "We couldn't reach brokAId.", next: "Check your connection, then try again." },
  server: { title: "Something went wrong on our side.", next: "Try again in a minute. If it keeps happening, contact your broker." },
};

const BY_STATUS: Record<number, string> = { 400: "validation", 401: "operator_role_required", 403: "forbidden", 404: "not_found",
  409: "conflict", 413: "file_too_large", 422: "validation", 429: "rate_limited" };

/** Plain message for a status + server detail (a code like "claim_not_found", or free text). */
export function messageFor(status: number, detail?: unknown): Plain & { code: string } {
  const d = typeof detail === "string" ? detail : (detail as { code?: string } | null)?.code;
  if (d && ERROR_MESSAGES[d]) return { code: d, ...ERROR_MESSAGES[d] };
  if (d && /^[a-z_]+_not_found$/.test(d)) return { code: d, ...ERROR_MESSAGES.not_found };
  const code = BY_STATUS[status] || (status === 0 ? "network" : "server");
  if (typeof detail === "string" && detail && !/^[a-z_]+$/.test(detail) && status < 500) {
    return { code, title: detail.replace(/\.?$/, "."), next: ERROR_MESSAGES[code].next };
  }
  return { code, ...ERROR_MESSAGES[code] };
}

export const plainText = (p: Plain) => `${p.title} ${p.next}`;

/** FastAPI/pydantic 422 detail → {field: hint}. Field = last string in `loc` (body.raw_message → raw_message). */
export function fieldErrorsFrom422(detail: unknown, hints: Record<string, string> = {}): Record<string, string> {
  const out: Record<string, string> = {};
  if (!Array.isArray(detail)) return out;
  for (const e of detail as { loc?: unknown[]; msg?: string; type?: string }[]) {
    const loc = (e.loc || []).filter((x) => typeof x === "string" && x !== "body") as string[];
    const f = loc[loc.length - 1];
    if (!f || out[f]) continue;
    out[f] = hints[f] || hintFor(e.type || "", e.msg || "");
  }
  return out;
}

function hintFor(type: string, msg: string): string {
  if (/missing/.test(type)) return "This is needed. Fill it in to continue.";
  if (/too_short|min_length/.test(type)) return "Add a little more detail.";
  if (/too_long|max_length/.test(type)) return "This is too long. Shorten it and try again.";
  if (/date/.test(type)) return "Use a date like 21/03/2026.";
  if (/email/.test(type) || /email/i.test(msg)) return "Use an email like name@business.com.au.";
  if (/int|float|number|decimal/.test(type)) return "Use numbers only, like 25000.";
  return "Check this and try again.";
}
