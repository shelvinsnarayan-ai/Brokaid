/**
 * KNumber — right-aligned DM Mono number with optional up/down colour.
 */

interface Props {
  value: number | null | undefined;
  format?: "int" | "money" | "pct" | "seconds" | "raw";
  className?: string;
  muted?: boolean;
  delta?: number | null;
}

function fmt(v: number, kind: Props["format"]): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  switch (kind) {
    case "money":
      return new Intl.NumberFormat("en-AU", { maximumFractionDigits: 0 }).format(n);
    case "pct":
      return `${n.toFixed(1)}%`;
    case "seconds":
      if (n < 60) return `${n.toFixed(0)}s`;
      if (n < 3600) return `${(n / 60).toFixed(1)}m`;
      return `${(n / 3600).toFixed(1)}h`;
    case "int":
      return new Intl.NumberFormat("en-AU").format(Math.round(n));
    default:
      return n.toLocaleString("en-AU", { maximumFractionDigits: 3 });
  }
}

export function KNumber({ value, format = "raw", className, muted, delta }: Props) {
  const cls = [
    "k-num",
    muted ? "is-muted" : "",
    delta !== undefined ? (delta === null ? "is-muted" : delta > 0 ? "is-up" : delta < 0 ? "is-down" : "") : "",
    className || "",
  ].filter(Boolean).join(" ");
  if (value === null || value === undefined || !Number.isFinite(Number(value))) {
    return <span className={cls} data-testid="k-num-null">—</span>;
  }
  return <span className={cls}>{fmt(Number(value), format)}</span>;
}
