/** R3.9: the caller's devices with rename and remove. */
import { useState } from "react";

const API = (import.meta as any).env.VITE_API_BASE || "";
export interface Device { device_id: string; label: string; last_ok_at: string | null; this_device: boolean; key_current: boolean }

function Row({ d, onChange }: { d: Device; onChange: () => void }) {
  const [edit, setEdit] = useState(false);
  const [label, setLabel] = useState(d.label);
  const call = async (method: "PATCH" | "DELETE") => {
    const r = await fetch(`${API}/api/me/push/devices/${d.device_id}`, { method, credentials: "include", headers: { "Content-Type": "application/json" },
      body: method === "PATCH" ? JSON.stringify({ label }) : undefined });
    if (r.ok) { setEdit(false); onChange(); }
  };
  return (
    <li className="brokaid-pref" data-testid={`push-device-${d.device_id}`}>
      {edit ? <input value={label} maxLength={60} onChange={(e) => setLabel(e.target.value)} aria-label="Device name" data-testid={`push-device-label-input-${d.device_id}`} />
        : <span style={{ display: "grid" }}><span className="brokaid-pref__label" data-testid={`push-device-label-${d.device_id}`}>{d.label}{d.this_device ? " · this device" : ""}</span>
          <span className="brokaid-pref__note">{d.last_ok_at ? `Last alert accepted ${new Date(d.last_ok_at).toLocaleString("en-AU")}` : "No alert sent yet"}</span></span>}
      <span style={{ display: "flex", gap: 8, flexShrink: 0, whiteSpace: "nowrap" }}>
        {edit ? <button type="button" className="brokaid-bell__link" onClick={() => void call("PATCH")} disabled={!label.trim()} data-testid={`push-device-save-${d.device_id}`}>Save</button>
          : <button type="button" className="brokaid-bell__link" onClick={() => setEdit(true)} data-testid={`push-device-rename-${d.device_id}`}>Rename</button>}
        <button type="button" className="brokaid-bell__link" onClick={() => void call("DELETE")} data-testid={`push-device-remove-${d.device_id}`}>Remove</button>
      </span>
    </li>
  );
}

export function PushDevices({ devices, onChange }: { devices: Device[]; onChange: () => void }) {
  if (!devices.length) return <p className="brokaid-pref__note" data-testid="push-devices-empty">No devices are set up for alerts yet.</p>;
  return <ul data-testid="push-devices">{devices.map((d) => <Row key={d.device_id} d={d} onChange={onChange} />)}</ul>;
}
