/** R3.9: "Test alerts on this device". A device checklist, a test send to this device or all devices, results, and "Did it arrive?". */
import { useCallback, useEffect, useState } from "react";
import { currentSubscription, keyMatches, pushSupport, turnOnPush } from "../../lib/push";
import { PushDevices, type Device } from "./PushDevices";
import { IOS_INSTALL, NOT_ARRIVED_TIPS, deniedHelp } from "./pushTips";

const API = (import.meta as any).env.VITE_API_BASE || "";
interface Status { enabled: boolean; disabled_message: string | null; prefs_push: boolean; public_key: string | null; devices: Device[]; this_device: Device | null; tests_left: number }
interface Result { device_id: string; label: string; result: "delivered" | "expired" | "error"; message: string }
interface Check { id: string; ok: boolean; text: string; fix?: string; action?: "allow" | "subscribe" }

async function deviceState() {
  const support = pushSupport();
  const perm = support === "supported" ? Notification.permission : "default";
  const reg = support === "supported" ? await navigator.serviceWorker.getRegistration() : undefined;
  const sub = support === "supported" ? await currentSubscription() : null;
  return { support, perm, swActive: !!reg?.active, sub };
}
type DS = Awaited<ReturnType<typeof deviceState>>;

function checks(d: DS, s: Status): Check[] {
  const out: Check[] = [];
  out.push(d.support === "supported" ? { id: "support", ok: true, text: "This browser supports alerts" }
    : { id: "support", ok: false, text: "This browser can't show alerts here", fix: d.support === "ios_needs_install" ? IOS_INSTALL : "Try Chrome, Edge, Firefox, or Safari 16.4 or later." });
  if (d.support !== "supported") return out;
  out.push(d.perm === "granted" ? { id: "permission", ok: true, text: "Alerts are allowed for this site" }
    : d.perm === "denied" ? { id: "permission", ok: false, text: "Alerts are blocked for this site", fix: deniedHelp() }
      : { id: "permission", ok: false, text: "You haven't been asked to allow alerts yet", action: "allow" });
  out.push(d.swActive ? { id: "sw", ok: true, text: "The app's background helper is running" }
    : { id: "sw", ok: false, text: "The app's background helper isn't running", fix: "Reload this page. If it keeps happening, close and reopen the app." });
  const saved = !!(d.sub && s.this_device && s.this_device.key_current && keyMatches(d.sub, s.public_key));
  if (d.perm === "granted") out.push(saved ? { id: "subscription", ok: true, text: "This device is set up to receive alerts" }
    : { id: "subscription", ok: false, text: d.sub ? "This device's alert setup is out of date" : "This device isn't set up for alerts yet", action: "subscribe" });
  out.push(!s.enabled ? { id: "config", ok: false, text: s.disabled_message || "Phone alerts are switched off." }
    : s.prefs_push ? { id: "config", ok: true, text: "Phone and browser alerts are on in your settings" }
      : { id: "config", ok: false, text: "Phone and browser alerts are off in your settings", action: d.perm === "granted" ? "subscribe" : undefined });
  return out;
}

export function PushTestCard() {
  const [s, setS] = useState<Status | null>(null);
  const [d, setD] = useState<DS | null>(null);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<{ test_id: string; results: Result[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [fb, setFb] = useState<boolean | null>(null);
  const load = useCallback(async () => {
    const ds = await deviceState();
    const q = ds.sub ? `?endpoint=${encodeURIComponent(ds.sub.endpoint)}` : "";
    const r = await fetch(`${API}/api/me/push/status${q}`, { credentials: "include" });
    setD(ds); setS(r.ok ? await r.json() : null);
  }, []);
  useEffect(() => { void load(); }, [load]);
  if (!s || !d) return null;
  const list = checks(d, s);
  const subscribe = async () => { setBusy(true); setErr(null); const r = await turnOnPush(); if (r === "error") setErr("Alerts couldn't be turned on. Try again."); setBusy(false); void load(); };
  const send = async (scope: "this_device" | "all") => {
    setBusy(true); setErr(null); setRes(null); setFb(null);
    const r = await fetch(`${API}/api/me/push/test`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope, subscription_endpoint: d.sub?.endpoint }) });
    const j = await r.json().catch(() => ({}));
    if (r.ok) setRes(j); else setErr(typeof j.detail?.detail === "string" ? j.detail.detail : "The test couldn't be sent. Try again.");
    setBusy(false); void load();
  };
  const feedback = async (arrived: boolean) => {
    setFb(arrived);
    await fetch(`${API}/api/me/push/test/${res!.test_id}/feedback`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ arrived }) });
  };
  const ready = s.enabled && s.prefs_push && s.tests_left > 0;
  const thisReady = ready && list.every((c) => c.ok);
  return (
    <section className="brokaid-alerts__prefs" data-testid="push-test-card">
      <h2>Test alerts on this device</h2>
      <ul data-testid="push-checks">
        {list.map((c) => (
          <li key={c.id} className="brokaid-pref" data-testid={`push-check-${c.id}`} data-ok={c.ok}>
            <span style={{ display: "grid", gridTemplateColumns: "22px 1fr", gap: 6 }}>
              <span aria-hidden style={{ color: c.ok ? "#1F9D55" : "#C2410C", fontWeight: 700 }}>{c.ok ? "✓" : "!"}</span>
              <span style={{ display: "grid" }}><span className="brokaid-pref__label">{c.text}</span>{c.fix ? <span className="brokaid-pref__note" data-testid={`push-check-fix-${c.id}`}>{c.fix}</span> : null}</span>
            </span>
            {c.action === "allow" ? <button type="button" className="brokaid-bell__link" disabled={busy || !s.enabled} onClick={() => void subscribe()} data-testid="push-allow">Allow alerts</button> : null}
            {c.action === "subscribe" && s.enabled ? <button type="button" className="brokaid-bell__link" disabled={busy} onClick={() => void subscribe()} data-testid={`push-subscribe-${c.id}`}>Turn on alerts for this device</button> : null}
          </li>
        ))}
      </ul>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 12 }}>
        <button type="button" className="k-btn k-btn--primary" disabled={busy || !thisReady} onClick={() => void send("this_device")} data-testid="push-test-this">Send test to this device</button>
        <button type="button" className="k-btn" disabled={busy || !ready || !s.devices.length} onClick={() => void send("all")} data-testid="push-test-all">Send test to all my devices ({s.devices.length})</button>
      </div>
      <p className="brokaid-pref__note" data-testid="push-tests-left">{s.tests_left} of 5 tests left this hour. Tests are sent straight away, even during quiet hours.</p>
      {err ? <p role="alert" className="brokaid-pref__note" data-testid="push-test-error">{err}</p> : null}
      {res ? (
        <div data-testid="push-test-results">
          <ul>{res.results.map((r) => <li key={r.device_id} className="brokaid-pref" data-testid={`push-result-${r.device_id}`} data-result={r.result}>
            <span className="brokaid-pref__label">{r.label}</span><span className="brokaid-pref__note">{r.result === "delivered" ? "✓ " : ""}{r.message}</span></li>)}</ul>
          {res.results.some((r) => r.result === "delivered") ? <p style={{ display: "flex", gap: 8, alignItems: "center" }}>Did it arrive?
            <button type="button" className="k-btn" onClick={() => void feedback(true)} aria-pressed={fb === true} data-testid="push-arrived-yes">Yes</button>
            <button type="button" className="k-btn" onClick={() => void feedback(false)} aria-pressed={fb === false} data-testid="push-arrived-no">No</button></p> : null}
          {fb === true ? <p className="brokaid-pref__note" data-testid="push-arrived-thanks">Alerts work on this device.</p> : null}
          {fb === false ? <ul className="brokaid-pref__note" data-testid="push-tips">{NOT_ARRIVED_TIPS.map((t) => <li key={t}>{t}</li>)}</ul> : null}
        </div>
      ) : null}
      <h3 style={{ marginTop: 16 }}>My devices</h3>
      <PushDevices devices={s.devices} onChange={() => void load()} />
    </section>
  );
}
