/** R0.11A — in-app alerts for the signed-in user (client or broker). Polled every 30s and on focus. */
import { useEffect } from "react";
import { create } from "zustand";

const API = (import.meta as any).env.VITE_API_BASE || "";

export interface Alert {
  id: string; kind: string; title: string; body: string; link: string; count: number;
  created_at: string; updated_at: string; read: boolean;
}

interface AlertsState {
  count: number;
  items: Alert[];
  refresh: () => Promise<void>;
  load: (limit?: number) => Promise<void>;
  markRead: (id: string) => Promise<void>;
  markAll: () => Promise<void>;
}

const post = (path: string) => fetch(`${API}${path}`, { method: "POST", credentials: "include" }).catch(() => null);

export const useAlertsStore = create<AlertsState>((set, get) => ({
  count: 0,
  items: [],
  refresh: async () => {
    try {
      const r = await fetch(`${API}/api/notifications/unread-count`, { credentials: "include" });
      if (r.ok) set({ count: (await r.json()).count || 0 });
    } catch {
      // best effort
    }
  },
  load: async (limit = 20) => {
    try {
      const r = await fetch(`${API}/api/notifications?limit=${limit}`, { credentials: "include" });
      if (r.ok) set({ items: (await r.json()).notifications || [] });
    } catch {
      // best effort
    }
  },
  markRead: async (id) => {
    await post(`/api/notifications/${id}/read`);
    set({ items: get().items.map((a) => (a.id === id ? { ...a, read: true } : a)) });
    await get().refresh();
  },
  markAll: async () => {
    await post("/api/notifications/read-all");
    set({ items: get().items.map((a) => ({ ...a, read: true })), count: 0 });
  },
}));

export function useAlertsPoll() {
  const refresh = useAlertsStore((s) => s.refresh);
  useEffect(() => {
    void refresh();
    const t = window.setInterval(() => void refresh(), 30000);
    const onFocus = () => document.visibilityState === "visible" && void refresh();
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.clearInterval(t);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [refresh]);
}

export function ago(iso: string, now = Date.now()): string {
  const m = Math.max(0, Math.round((now - new Date(iso).getTime()) / 60000));
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  return `${d} day${d === 1 ? "" : "s"} ago`;
}

export function stamp(iso: string): string {
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  const h = d.getHours() % 12 || 12;
  return `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()} ${h}:${p(d.getMinutes())} ${d.getHours() < 12 ? "am" : "pm"}`;
}
