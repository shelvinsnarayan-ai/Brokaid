import { useCallback, useEffect, useState } from "react";
import { authCall } from "../../../lib/authApi";

export const API = import.meta.env.VITE_API_BASE || "";
export const get = <T = any>(p: string) => authCall<T>(p, undefined, "GET");
export const post = authCall;
export const day = (v?: string | null) => (v ? new Date(v).toLocaleDateString("en-AU") : "—");
export const dt = (v?: string | null) => (v ? new Date(v).toLocaleString("en-AU", { dateStyle: "short", timeStyle: "short" }) : "—");

export function useLoad<T = any>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => { get<T>(path).then(setData).catch((e) => setErr(e.message)); }, [path]);
  useEffect(load, [load]);
  return { data, err, load, setErr };
}
