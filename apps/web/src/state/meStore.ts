/**
 * Client-app "me" store. Hydrated on mount via GET /api/c/me.
 * Drives auth guard, AmountDisplay, PreviewBanner, tab visibility.
 */
import { create } from "zustand";

const API = import.meta.env.VITE_API_BASE || "";

export interface Me {
  user: {
    user_id: string;
    email: string | null;
    display_name: string | null;
    kind: "operator" | "client" | null;
  };
  session: {
    session_id: string;
    active_role: string;
    tenant_id: string;
    client_id: string | null;
  };
  linked_client: {
    client_id: string;
    display_name: string;
    industry: string;
    domicile: string;
    jurisdiction_code: "AU" | "SG";
  } | null;
  client_prefs: { jurisdiction?: "AU" | "SG" };
}

export interface Pack {
  code: string;
  display_name: string;
  currency_code: string;
  currency_symbol: string;
  rail_label: string;
  locale: string;
  regulator: string;
  disclosure_strings: Record<string, string>;
}

interface MeState {
  me: Me | null;
  packs: Record<string, Pack>;
  loading: boolean;
  error: string | null;
  load: () => Promise<void>;
  logout: () => Promise<void>;
  setPreview: (code: "AU" | "SG" | null) => Promise<void>;
}

export const useMeStore = create<MeState>((set, get) => ({
  me: null,
  packs: {},
  loading: false,
  error: null,
  async load() {
    set({ loading: true, error: null });
    try {
      const [meRes, jRes] = await Promise.all([
        fetch(`${API}/api/c/me`, { credentials: "include" }),
        fetch(`${API}/api/jurisdictions`),
      ]);
      const packs: Record<string, Pack> = {};
      if (jRes.ok) {
        for (const p of (await jRes.json()).jurisdictions as Pack[])
          packs[p.code] = p;
      }
      if (meRes.status === 401) {
        set({ me: null, packs, loading: false });
        return;
      }
      if (!meRes.ok) {
        set({ error: `me ${meRes.status}`, loading: false, packs });
        return;
      }
      set({ me: (await meRes.json()) as Me, packs, loading: false });
    } catch (e: any) {
      set({ error: String(e), loading: false });
    }
  },
  async logout() {
    // No dedicated endpoint yet — clear cookie client-side + reload state.
    document.cookie = "brokaid_session=; Max-Age=0; path=/";
    set({ me: null });
  },
  async setPreview(code) {
    await fetch(`${API}/api/c/preferences`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jurisdiction: code }),
    });
    await get().load();
  },
}));

/** Which jurisdiction pack drives the UI right now. */
export function usePack(): Pack | null {
  const { me, packs } = useMeStore((s) => ({ me: s.me, packs: s.packs }));
  const previewCode = me?.client_prefs?.jurisdiction ?? null;
  const actualCode = me?.linked_client?.jurisdiction_code ?? "AU";
  const active = previewCode ?? actualCode;
  return packs[active] ?? null;
}

/** True when preview != actual — drives the PreviewBanner. */
export function isPreviewMode(me: Me | null): boolean {
  if (!me) return false;
  const preview = me.client_prefs?.jurisdiction;
  const actual = me.linked_client?.jurisdiction_code ?? "AU";
  return Boolean(preview && preview !== actual);
}
