import { useEffect, useState } from "react";

const API = import.meta.env.VITE_API_BASE || "";

type Persona = { slug: string; name: string; seat: string; email: string; route: string };

// Preview-only: compiled out unless ENVIRONMENT is development/preview/test, and hidden unless the backend lists personas.
export function DemoSignIn() {
  const [list, setList] = useState<Persona[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/api/auth/dev/personas`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setList(d?.personas ?? null))
      .catch(() => setList(null));
  }, []);

  if (!list?.length) return null;

  const go = async (p: Persona) => {
    setBusy(p.slug);
    setErr(null);
    const r = await fetch(`${API}/api/auth/dev/impersonate`, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: p.email }),
    }).catch(() => null);
    if (r?.ok) window.location.assign(p.route);
    else { setErr(`Couldn't sign in as ${p.name} (${r?.status ?? "network"}).`); setBusy(null); }
  };

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
      {err ? <p className="auth-fine" role="alert" data-testid="demo-signin-error">{err}</p> : null}
    </section>
  );
}
