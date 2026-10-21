/** R0.9 — unread broker replies for the signed-in client user (per user, per thread). */
import { create } from "zustand";

const API = import.meta.env.VITE_API_BASE || "";

interface UnreadState {
  count: number;
  threads: { thread_id: string; unread: number; reply_ids?: string[]; claim_id?: string | null }[];
  refresh: () => Promise<void>;
  markSeen: (only?: string[]) => Promise<void>;
}

export const useUnreadStore = create<UnreadState>((set, get) => ({
  count: 0,
  threads: [],
  refresh: async () => {
    try {
      const r = await fetch(`${API}/api/c/ask/unread`, { credentials: "include" });
      if (r.ok) {
        const b = await r.json();
        set({ count: b.count || 0, threads: b.threads || [] });
      }
    } catch {
      // best effort
    }
  },
  markSeen: async (only?: string[]) => {
    // R3.4: by default only the main broker thread(s); claim threads are marked seen from their own page
    const ids = get().threads.filter((t) => (only ? only.includes(t.thread_id) : !t.claim_id)).map((t) => t.thread_id);
    await Promise.all(ids.map((thread_id) => fetch(`${API}/api/c/ask/seen`, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ thread_id }),
    }).catch(() => null)));
    const rest = get().threads.filter((t) => !ids.includes(t.thread_id));
    set({ count: rest.reduce((n, t) => n + t.unread, 0), threads: rest });
  },
}));

export const unreadLabel = (n: number) => `${n} new ${n === 1 ? "reply" : "replies"} from your broker`;

export function UnreadCount({ n, testid }: { n: number; testid: string }) {
  if (!n) return null;
  return <span className="brokaid-unread" role="status" aria-label={unreadLabel(n)} data-testid={testid}>{n}</span>;
}

export function UnreadDot({ n, testid }: { n: number; testid: string }) {
  if (!n) return null;
  return <span className="brokaid-unread-dot" role="status" aria-label={unreadLabel(n)} data-testid={testid} />;
}
