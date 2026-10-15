import type { CSSProperties, ReactNode } from "react";

export const API = (import.meta as any).env.VITE_API_BASE || "";
export const card: CSSProperties = { border: "1px solid var(--line, #1E2E5A)", padding: 16, marginBottom: 20 };
export const muted: CSSProperties = { fontSize: 12, color: "var(--slate)" };
export const cell: CSSProperties = { padding: "8px 10px", borderTop: "1px solid var(--line, #1E2E5A)", verticalAlign: "top" };

export async function getJson(path: string): Promise<{ ok: boolean; status: number; data: any }> {
  const r = await fetch(`${API}${path}`, { credentials: "include" });
  return { ok: r.ok, status: r.status, data: r.ok ? await r.json() : null };
}

export function Basis({ b, testid }: { b: string; testid?: string }) {
  return (<>{" "}
    <span data-testid={testid} title="Where this number comes from"
      style={{ fontSize: 10, letterSpacing: 0.4, textTransform: "uppercase", border: "1px solid var(--line, #1E2E5A)",
        padding: "1px 6px", marginLeft: 8, color: "var(--slate)", fontFamily: "'IBM Plex Mono', monospace" }}>
      {b}
    </span>
  </>);
}

export function PageHead({ eyebrow, title, children }: { eyebrow: string; title: string; children?: ReactNode }) {
  return (
    <header style={{ marginBottom: 20 }}>
      <p style={muted}>{eyebrow}</p>
      <h1 style={{ fontSize: 28, margin: "4px 0 8px" }}>{title}</h1>
      {children}
    </header>
  );
}
