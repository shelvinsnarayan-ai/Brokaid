/** R0.9 — unread broker replies for the signed-in client user (per user, per thread). */
import { create } from "zustand";

const API = import.meta.env.VITE_API_BASE || "";

interface UnreadState {
  count: number;
  threads: { thread_id: string; unread: number; reply_ids?: string[] }[];
  refresh: () => Promise<void>;
  markSeen: () => Promise<void>;
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
  markSeen: async () => {
    const ids = get().threads.map((t) => t.thread_id);
    await Promise.all(ids.map((thread_id) => fetch(`${API}/api/c/ask/seen`, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ thread_id }),
    }).catch(() => null)));
    set({ count: 0, threads: [] });
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
