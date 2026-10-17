import { authCall } from "../../../lib/authApi";

export const api = authCall;
export const get = <T = any>(p: string) => authCall<T>(p, undefined, "GET");

export interface Quote { quote_id: string; insurer: string; premium: string; excess: string; key_terms: string[]; data_basis: string; mocked: boolean }
export interface Option {
  quote_id: string; insurer: string; premium: string; premium_change: string; premium_change_pct: string | null; excess: string;
  excess_change: string; limits_same: boolean; key_differences: string[]; data_basis: string; mocked: boolean;
}
export interface Renewal {
  renewal_id: string; policy_id: string; client_name: string; product: string; stage: string; current: string; stage_label: string;
  expiry: string; needs_review: boolean; issues: string[]; answers: any; accepted_changes: any[]; quotes: Quote[]; lapse_alerted: boolean;
  recommendation: null | { quote_id: string; text: string; reasons: string[]; tier: string; rule_id: string; reason: string;
    principal_approved_by: string | null; released_at: string | null };
  decision: null | { choice: string; premium?: string; insurer?: string }; new_policy_id: string | null;
  history: { from: string; to: string; at: string; note: string | null }[];
}
export interface Detail {
  renewal: Renewal; comparison: { expiring: { insurer: string; premium: string; excess: string; expiry: string }; options: Option[] };
  payable: null | { crn: string; amount: string; state: string }; packet: null | { lines: { label: string; value: string }[]; text: string };
  general_advice_warning: string;
}
export const aud = (v: string | number) => `$${Number(v).toLocaleString("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export const signed = (v: string | number) => `${Number(v) > 0 ? "+" : Number(v) < 0 ? "−" : ""}${aud(Math.abs(Number(v)))}`;
