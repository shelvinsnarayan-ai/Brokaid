import { create } from "zustand";

interface OperatorSession {
  user: { user_id: string; email: string | null; display_name: string | null };
  session: { session_id: string; active_role: string; tenant_id: string };
  brokerage?: { display_name?: string };
}

interface Toast {
  id: number;
  message: string;
  kind: "info" | "verify" | "alert";
}

interface State {
  session: OperatorSession | null;
  loading: boolean;
  toasts: Toast[];
  loadSession: () => Promise<void>;
  toast: (message: string, kind?: Toast["kind"]) => void;
  dismiss: (id: number) => void;
}

const API = import.meta.env.VITE_API_BASE || "";

let toastSeq = 1;

export const useOperatorStore = create<State>((set, get) => ({
  session: null,
  loading: false,
  toasts: [],

  async loadSession() {
    set({ loading: true });
    try {
      // Uses /api/c/me for the linked_client, but for operators we hit
      // /api/o/kpi/summary and infer role from its 200 vs 401.
      // Cleanest path: read the sessions cookie via /api/c/me (works
      // for both kinds — returns user_kind).
      const res = await fetch(`${API}/api/c/me`, { credentials: "include" });
      if (!res.ok) {
        set({ session: null, loading: false });
        return;
      }
      const body = await res.json();
      set({
        session: {
          user: body.user,
          session: body.session,
        },
        loading: false,
      });
    } catch {
      set({ session: null, loading: false });
    }
  },

  toast(message, kind = "info") {
    const id = toastSeq++;
    set((s) => ({ toasts: [...s.toasts, { id, message, kind }] }));
    setTimeout(() => get().dismiss(id), 4000);
  },

  dismiss(id) {
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
  },
}));
