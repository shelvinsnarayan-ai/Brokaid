import { authCall } from "../../../lib/authApi";

const API = import.meta.env.VITE_API_BASE || "";

export type Entity = "clients" | "insurers" | "policies" | "vehicles" | "contacts";
export const ENTITY_LABEL: Record<Entity, string> = {
  clients: "Clients", insurers: "Insurers", policies: "Policies", vehicles: "Vehicles", contacts: "Contacts",
};
export interface Sheet {
  name: string; headers: string[]; row_count: number; entity: Entity;
  mapping: Record<string, string | null>; suggested: Record<Entity, Record<string, string | null>>; sample: string[][];
}
export interface Upload { file_id: string; filename: string; sha256: string; size: number; sheets: Sheet[]; profile: { name: string } | null }
export interface Counts { rows: number; ok: number; warn: number; error: number; create: number; update: number; unchanged: number }
export interface Run {
  run_id: string; filename: string; file_sha: string; status: string; created_at: string; created_by_name?: string;
  counts: Record<string, Counts>; progress: { done: number; total: number };
}
export interface Row {
  row_id: string; sheet: string; row: number; entity: Entity; key: string | null; status: "ok" | "warn" | "error";
  issues: { level: string; text: string }[]; action: string | null; diff: Record<string, [unknown, unknown]>;
}
export type Fields = Record<Entity, { field: string; required: boolean }[]>;

export async function uploadFile(file: File): Promise<Upload> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`${API}/api/o/import/files`, { method: "POST", credentials: "include", body: fd });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Upload failed. Please try again.");
  return data as Upload;
}

export const errorsCsvUrl = (runId: string) => `${API}/api/o/import/runs/${runId}/errors.csv`;
export const api = authCall;
export const label = (f: string) => f.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
