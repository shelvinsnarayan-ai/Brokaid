import { authCall } from "../../../lib/authApi";

const API = import.meta.env.VITE_API_BASE || "";
export const api = authCall;
export const get = <T = any>(p: string) => authCall<T>(p, undefined, "GET");
export const fileUrl = (p: string) => `${API}/api/${p}`;

export interface Txn {
  txn_id: string; date: string; amount: string; description: string; balance: string | null; direction: "in" | "out"; status: string;
  match: { payable_id: string; crn: string; kind: string; review_required: boolean; reviewed: boolean } | null;
  suggestions: { payable_id: string; crn: string; client: string; amount: string; score: number; why: string }[];
}
export interface Payable { payable_id: string; crn: string; amount: string; received: string; state: string; client?: string; due_date: string; policy_id: string }

export async function uploadStatement(file: File, mapping?: Record<string, string>) {
  const fd = new FormData();
  fd.append("file", file);
  if (mapping) fd.append("mapping", JSON.stringify(mapping));
  const res = await fetch(`${API}/api/o/bank/statements`, { method: "POST", credentials: "include", body: fd });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Upload failed. Please try again.");
  return data;
}

export const STATUS_LABEL: Record<string, string> = {
  matched: "Matched", suggested: "Suggested", unmatched: "Unmatched", partial: "Part paid", overpaid: "Overpaid",
  outgoing: "Money out (not recorded)", allocated: "Money out (recorded)",
};
export const money = (v: string | number) => `${Number(v) < 0 ? "−" : ""}$${Math.abs(Number(v)).toLocaleString("en-AU", { minimumFractionDigits: 2 })}`;
