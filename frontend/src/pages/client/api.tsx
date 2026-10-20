import { useEffect, useState } from "react";

const API = (import.meta as any).env.VITE_API_BASE || "";

export async function cget<T>(path: string): Promise<T> {
  const r = await fetch(`${API}/api/${path}`, { credentials: "include" });
  if (!r.ok) throw new Error(r.status === 404 ? "Not found." : "Couldn't load this. Please try again.");
  return r.json();
}

export function useC<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!path) return;
    let live = true;
    setData(null); setErr(null);
    cget<T>(path).then((d) => live && setData(d)).catch((e) => live && setErr(e.message));
    return () => { live = false; };
  }, [path]);
  return { data, err };
}

export function Skel({ h = 64, n = 3 }: { h?: number; n?: number }) {
  return <div aria-busy="true" aria-label="Loading">{Array.from({ length: n }, (_, i) => <div key={i} className="mer-skel" style={{ height: h, margin: "10px 0" }} />)}</div>;
}

export function Copy({ text, label, testid }: { text: string; label: string; testid: string }) {
  const [done, setDone] = useState(false);
  return (
    <button type="button" className="mer-copy" data-testid={testid} aria-label={`Copy ${label}`}
      onClick={() => { void navigator.clipboard?.writeText(text); setDone(true); window.setTimeout(() => setDone(false), 1500); }}>
      {done ? "Copied" : `Copy ${label}`}
    </button>
  );
}

export function Ico({ name }: { name: string }) {
  const d: Record<string, string> = {
    car: "M5 11l1.5-4.5A2 2 0 0 1 8.4 5h7.2a2 2 0 0 1 1.9 1.5L19 11m-14 0h14v5H5zm2 5v2m10-2v2",
    briefcase: "M4 8h16v11H4zM9 8V6a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2",
    home: "M4 11l8-6 8 6v8H4z M10 19v-5h4v5",
    shield: "M12 3l7 3v6c0 4-3 7-7 9-4-2-7-5-7-9V6z",
    claim: "M12 3l7 3v6c0 4-3 7-7 9-4-2-7-5-7-9V6z M9 12l2 2 4-4",
    change: "M4 7h12l-3-3m3 13H4l3 3",
    cert: "M6 3h9l3 3v15H6z M9 12h6 M9 16h6",
    doc: "M6 3h9l3 3v15H6z",
    chat: "M4 5h16v11H8l-4 4z",
    pay: "M3 7h18v10H3z M3 11h18",
    plus: "M12 5v14M5 12h14",
    house: "M4 11l8-6 8 6v8h-5v-5h-6v5H4z",
    list: "M5 7h14M5 12h14M5 17h9",
    user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM5 20a7 7 0 0 1 14 0",
  };
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d[name] || d.shield} />
    </svg>
  );
}
