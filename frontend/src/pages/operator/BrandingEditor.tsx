/** R0.15 — brokerage email branding editor (own tenant only; Operator edits, Principal views). */
import { useEffect, useMemo, useState } from "react";

const API = (import.meta as any).env.VITE_API_BASE || "";
const muted: React.CSSProperties = { fontSize: 12, color: "var(--slate)" };
const field: React.CSSProperties = { display: "grid", gap: 4, marginBottom: 12 };
const input: React.CSSProperties = { width: "100%", maxWidth: 420, boxSizing: "border-box" };

type Form = { sender_name: string; reply_to: string | null; brand_colour: string; logo_url: string | null; allowlist: string[] };
type View = { tenant_name: string; settings: Form; can_edit: boolean; email_enabled: boolean; min_contrast: number };

const HEX = /^#[0-9A-Fa-f]{6}$/;
function contrastWithWhite(hex: string): number {
  const ch = (i: number) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  const l = 0.2126 * ch(1) + 0.7152 * ch(3) + 0.0722 * ch(5);
  return Math.round((1.05 / (l + 0.05)) * 100) / 100;
}

function errMsg(detail: any): Record<string, string> {
  if (detail?.errors) return detail.errors;
  return { _: detail?.message || "Something went wrong. Please try again." };
}

function ColourField({ value, min, disabled, onChange }: { value: string; min: number; disabled: boolean; onChange: (v: string) => void }) {
  const ok = HEX.test(value);
  const ratio = ok ? contrastWithWhite(value) : 0;
  return (
    <label style={field}>
      <span>Brand colour</span>
      <span style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input type="color" value={ok ? value : "#000000"} disabled={disabled} onChange={(e) => onChange(e.target.value.toUpperCase())}
          aria-label="Pick brand colour" data-testid="brand-colour-picker" />
        <input value={value} disabled={disabled} onChange={(e) => onChange(e.target.value.trim())} style={{ width: 100 }}
          aria-label="Brand colour hex" data-testid="brand-colour-input" />
        <span data-testid="brand-colour-swatch" style={{ background: ok ? value : "transparent", color: "#fff", padding: "6px 14px",
          borderRadius: 999, fontSize: 13, fontWeight: 700, border: "1px solid var(--line, #1E2E5A)" }}>Open the app</span>
      </span>
      <span data-testid="brand-contrast" style={{ ...muted, color: ok && ratio < min ? "#F08A7E" : "var(--slate)" }}>
        {!ok ? "Use a hex colour like #0F1F3D."
          : ratio < min ? `White button text on ${value} has a contrast of ${ratio}:1. It needs at least ${min}:1 so everyone can read it. Pick a darker colour.`
            : `Contrast with white button text: ${ratio}:1 (passes ${min}:1).`}
      </span>
    </label>
  );
}

function LogoField({ url, disabled, onChange }: { url: string | null; disabled: boolean; onChange: (v: string | null) => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const upload = async (f: File) => {
    setBusy(true); setErr(null);
    const fd = new FormData();
    fd.append("file", f);
    const r = await fetch(`${API}/api/o/tenant/branding/logo`, { method: "POST", credentials: "include", body: fd });
    const b = await r.json().catch(() => ({}));
    if (r.ok) onChange(b.logo_url); else setErr(errMsg(b.detail)._ || "Upload failed.");
    setBusy(false);
  };
  return (
    <div style={field}>
      <span>Logo</span>
      {url ? <img src={url} alt="Brokerage logo" data-testid="brand-logo-img" style={{ maxWidth: 240, maxHeight: 60, background: "#fff", padding: 6 }} />
        : <span style={muted} data-testid="brand-logo-none">No logo. Emails show the sender name as text.</span>}
      {!disabled && (
        <span style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input type="file" accept="image/png,image/jpeg" disabled={busy} data-testid="brand-logo-file"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} />
          {url && <button type="button" className="k-btn" onClick={() => onChange(null)} data-testid="brand-logo-remove">Remove logo</button>}
        </span>
      )}
      <span style={muted}>PNG or JPG, up to 1 MB. Wider than 600px is resized. SVG isn't accepted because most email apps block it.</span>
      {busy && <span style={muted}>Uploading…</span>}
      {err && <span style={{ ...muted, color: "#F08A7E" }} data-testid="brand-logo-error">{err}</span>}
    </div>
  );
}

function EmailPreview({ form, dirty }: { form: Form; dirty: boolean }) {
  const [html, setHtml] = useState("");
  const [mode, setMode] = useState("saved");
  useEffect(() => {
    const t = setTimeout(() => {
      void fetch(`${API}/api/o/tenant/branding/preview`, {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(dirty ? { draft: form } : {}),
      }).then(async (r) => {
        if (r.ok) { const b = await r.json(); setHtml(b.html); setMode(b.mode); } else setMode("invalid");
      });
    }, 400);
    return () => clearTimeout(t);
  }, [form, dirty]);
  return (
    <div style={field}>
      <span>Email preview <span style={muted} data-testid="brand-preview-mode">({mode === "draft" ? "unsaved changes"
        : mode === "invalid" ? "unsaved changes need fixing — showing the last valid preview" : "saved settings"})</span></span>
      <iframe title="Email preview" srcDoc={html} sandbox="" data-testid="brand-preview"
        style={{ width: "100%", maxWidth: 600, height: 330, border: "1px solid var(--line, #1E2E5A)", background: "#F4F6FA" }} />
    </div>
  );
}

export function BrandingEditor() {
  const [view, setView] = useState<View | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [allowText, setAllowText] = useState("");
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [note, setNote] = useState<string | null>(null);
  const load = async () => {
    const r = await fetch(`${API}/api/o/tenant/branding`, { credentials: "include" });
    if (!r.ok) return;
    const v: View = await r.json();
    setView(v); setForm(v.settings); setAllowText(v.settings.allowlist.join("\n"));
  };
  useEffect(() => { void load(); }, []);
  const draft = useMemo(() => form && { ...form, allowlist: allowText.split(/[\s,]+/).filter(Boolean) }, [form, allowText]);
  if (!view || !form || !draft) return null;
  const ro = !view.can_edit;
  const dirty = JSON.stringify(draft) !== JSON.stringify(view.settings);
  const lowContrast = !HEX.test(form.brand_colour) || contrastWithWhite(form.brand_colour) < view.min_contrast;
  const set = (k: keyof Form, v: any) => { setForm({ ...form, [k]: v }); setNote(null); };
  const save = async () => {
    setErrs({}); setNote(null);
    const r = await fetch(`${API}/api/o/tenant/branding`, {
      method: "PUT", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(draft) });
    const b = await r.json().catch(() => ({}));
    if (!r.ok) return setErrs(errMsg(b.detail));
    setView(b); setForm(b.settings); setAllowText(b.settings.allowlist.join("\n"));
    setNote(b.changed.length ? `Saved: ${b.changed.join(", ").replace(/_/g, " ")}.` : "No changes to save.");
  };
  const testEmail = async () => {
    setNote(null);
    const r = await fetch(`${API}/api/o/tenant/branding/test-email`, {
      method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(dirty ? { draft } : {}) });
    const b = await r.json().catch(() => ({}));
    if (!r.ok) return setErrs(errMsg(b.detail));
    setNote(`Test email (${b.mode === "draft" ? "unsaved changes" : "saved settings"}): ${b.email}${b.message_id ? ` · id ${b.message_id}` : ""}`);
  };
  const err = (k: string) => errs[k] && <span style={{ ...muted, color: "#F08A7E" }} data-testid={`brand-error-${k}`}>{errs[k]}</span>;
  return (
    <div data-testid="brand-editor" style={{ margin: "8px 0 16px" }}>
      <p style={{ ...muted, margin: "0 0 12px" }}>
        Email branding for {view.tenant_name}{ro ? " (view only)" : ""}. Email on this site: <span data-testid="brand-email-enabled">{view.email_enabled ? "On" : "Off"}</span>.
      </p>
      <label style={field}><span>Sender name</span>
        <input value={form.sender_name} disabled={ro} onChange={(e) => set("sender_name", e.target.value)} style={input} data-testid="brand-sender-input" />
        {err("sender_name")}</label>
      <label style={field}><span>Reply-to (optional)</span>
        <input value={form.reply_to ?? ""} disabled={ro} onChange={(e) => set("reply_to", e.target.value || null)} style={input}
          placeholder="help@yourbrokerage.com.au" data-testid="brand-reply-input" />
        {err("reply_to")}</label>
      <ColourField value={form.brand_colour} min={view.min_contrast} disabled={ro} onChange={(v) => set("brand_colour", v)} />
      {err("brand_colour")}
      <LogoField url={form.logo_url} disabled={ro} onChange={(v) => set("logo_url", v)} />
      {err("logo_url")}
      <label style={field}><span>Allow-list</span>
        <span style={{ ...muted, color: "#F2B01E" }} data-testid="brand-allowlist-warning">
          Warning: this controls who can receive demo email. Only these addresses (or *@domain) are ever sent to.
        </span>
        <textarea value={allowText} disabled={ro} onChange={(e) => { setAllowText(e.target.value); setNote(null); }} rows={3}
          style={input} data-testid="brand-allowlist-input" />
        {err("allowlist")}</label>
      <EmailPreview form={draft} dirty={dirty} />
      {!ro && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <button className="k-btn" onClick={() => void save()} disabled={!dirty || lowContrast} data-testid="brand-save">Save branding</button>
          <button className="k-btn" onClick={() => void testEmail()} data-testid="brand-test-email">
            Send test email to me ({dirty ? "unsaved changes" : "saved settings"})</button>
        </div>
      )}
      {err("_")}
      {note && <p style={{ ...muted, marginTop: 8 }} data-testid="brand-note">{note}</p>}
    </div>
  );
}
