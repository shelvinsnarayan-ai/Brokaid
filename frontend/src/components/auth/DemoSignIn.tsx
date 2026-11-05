import { useEffect, useState } from "react";

const API = import.meta.env.VITE_API_BASE || "";

type Persona = { slug: string; name: string; seat: string; email: string; route: string };
export const SANDBOX_CONFIRM = "This clears the made-up business details and puts all 5 drafts back to blank. Continue?";

// R3.10: same list as backend devmode.PREVIEW_HOSTS / PREVIEW_SUFFIXES.
export const isPreviewHost = (h: string) => ["localhost", "127.0.0.1"].includes(h) || h.endsWith(".preview.emergentagent.com");

// Preview-only: compiled out unless ENVIRONMENT is development/preview/test, and hidden unless the backend lists personas.
export function DemoSignIn() {
  const [list, setList] = useState<Persona[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const previewHost = isPreviewHost(window.location.hostname);
  useEffect(() => {
    if (!previewHost) return;  // R3.10: never on a public host, whatever the env says
    fetch(`${API}/api/auth/dev/personas`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setList(d?.personas ?? null))
      .catch(() => setList(null));
  }, []);

  if (!previewHost || !list?.length) return null;

  const go = async (p: Persona, to?: string) => {
    setBusy(p.slug);
    setErr(null);
    const r = await fetch(`${API}/api/auth/dev/impersonate`, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: p.email }),
    }).catch(() => null);
    if (r?.ok) window.location.assign(to ?? p.route);
    else { setErr(`Couldn't sign in as ${p.name} (${r?.status ?? "network"}).`); setBusy(null); }
  };

  const action = async (slug: string, path: string, done: (d: any) => void) => {
    setBusy(slug);
    setErr(null);
    setNote(null);
    const r = await fetch(`${API}/api/auth/dev/demo/${path}`, { method: "POST", credentials: "include" }).catch(() => null);
    const d = r ? await r.json().catch(() => ({})) : {};
    if (r?.ok) done(d);
    else { setErr(`Demo action failed (${r?.status ?? "network"}): ${d?.detail ?? ""}`); setBusy(null); }
  };
  const kai = list.find((p) => p.slug === "kai");

  return (
    <section className="demo-signin" data-testid="demo-signin-panel" aria-label="Demo sign-in (preview only)">
      <h2 className="demo-signin__title">Demo sign-in (preview only)</h2>
      <p className="auth-fine">Fictional Harbourline personas. Not available in production.</p>
      <div className="demo-signin__grid">
        {list.map((p) => (
          <button key={p.slug} type="button" className="auth-btn auth-btn--ghost demo-signin__btn" disabled={busy !== null}
            onClick={() => void go(p)} data-testid={`demo-signin-${p.slug}`}>
            <strong>{busy === p.slug ? "Signing in…" : p.name}</strong>
            <span>{p.seat}</span>
          </button>
        ))}
      </div>
      {kai ? (
        <div className="demo-signin__grid">
          <button type="button" className="auth-btn auth-btn--ghost demo-signin__btn" disabled={busy !== null}
            data-testid="demo-signin-kai-renewal"
            onClick={() => void action("renewal", "kai-renewal", (d) => go(kai, d.accept_path))}>
            <strong>{busy === "renewal" ? "Preparing… (about 10 s)" : "Prepare client renewal"}</strong>
            <span>Stops at accept, then signs in as the client</span>
          </button>
          <button type="button" className="auth-btn auth-btn--ghost demo-signin__btn" disabled={busy !== null}
            data-testid="demo-signin-kai-reset"
            onClick={() => void action("reset", "kai-reset", (d) => {
              setNote(`Client demo reset (ledger event ${String(d.ledger_event_id).slice(0, 8)}).`); setBusy(null);
            })}>
            <strong>{busy === "reset" ? "Resetting…" : "Reset client demo"}</strong>
            <span>Hilux back on the motor policy</span>
          </button>
        </div>
      ) : null}
      {list.some((p) => p.slug === "sandbox-principal") ? (
        <div className="demo-signin__grid">
          <button type="button" className="auth-btn auth-btn--ghost demo-signin__btn" disabled={busy !== null}
            data-testid="demo-signin-sandbox-reset"
            onClick={() => {
              if (!window.confirm(SANDBOX_CONFIRM)) return;
              void action("sandbox", "sandbox-reset", () => { setNote("BurMac sandbox reset: all 5 drafts are blank again."); setBusy(null); });
            }}>
            <strong>{busy === "sandbox" ? "Resetting…" : "Reset sandbox"}</strong>
            <span>BurMac sandbox back to blank drafts</span>
          </button>
        </div>
      ) : null}
      {note ? <p className="auth-fine" role="status" data-testid="demo-signin-note">{note}</p> : null}
      {err ? <p className="auth-fine" role="alert" data-testid="demo-signin-error">{err}</p> : null}
    </section>
  );
}
