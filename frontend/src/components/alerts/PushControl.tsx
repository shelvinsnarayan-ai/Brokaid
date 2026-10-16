/** R0.13 — Push row in Notification settings: per device, explains when push can't work here. */
import { useEffect, useState } from "react";
import { currentSubscription, pushSupport, turnOffPush, turnOnPush } from "../../lib/push";

const COPY = {
  ios_needs_install: "Add brokAId to your Home Screen to get alerts on iPhone.",
  unsupported: "This browser can't show push alerts.",
  blocked: "Push is blocked for this site. Allow notifications in your browser settings, then try again.",
};

export function PushControl({ available }: { available: boolean }) {
  const support = pushSupport();
  const [on, setOn] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => { void currentSubscription().then((s) => setOn(!!s)); }, []);
  const blocked = support === "supported" && Notification.permission === "denied";
  const msg = !available ? "Push alerts are not set up yet." : support !== "supported" ? COPY[support] : blocked ? COPY.blocked : null;
  const toggle = async () => {
    setBusy(true); setNote(null);
    if (on) { await turnOffPush(); setOn(false); }
    else {
      const r = await turnOnPush();
      setOn(r === "on");
      if (r === "blocked") setNote(COPY.blocked);
      if (r === "error") setNote("Push could not be turned on. Please try again.");
    }
    setBusy(false);
  };
  return (
    <li className="brokaid-pref" data-testid="pref-push">
      <span style={{ display: "grid" }}>
        <span className="brokaid-pref__label">Push</span>
        <span className="brokaid-pref__note" data-testid="pref-push-note">
          {msg || note || (on ? "On for this device." : "Off for this device.")}
        </span>
      </span>
      {!msg && on !== null && (
        <button type="button" className="brokaid-bell__link" onClick={() => void toggle()} disabled={busy}
          data-testid="pref-push-toggle">{busy ? "Working…" : on ? "Turn off on this device" : "Turn on push"}</button>
      )}
    </li>
  );
}
