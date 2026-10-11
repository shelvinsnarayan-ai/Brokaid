// AU display formats: dates dd/mm/yyyy, money "AUD $2,550.00".
const pad = (n: number) => String(n).padStart(2, "0");

function toDate(v: string | number | Date | null | undefined): Date | null {
  if (v === null || v === undefined || v === "") return null;
  const d = typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) ? new Date(`${v}T00:00:00`) : new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function fmtDate(v: string | number | Date | null | undefined): string {
  const d = toDate(v);
  return d ? `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()}` : "—";
}

export function fmtDateTime(v: string | number | Date | null | undefined): string {
  const d = toDate(v);
  return d ? `${fmtDate(d)} ${pad(d.getHours())}:${pad(d.getMinutes())}` : "—";
}

export function fmtAud(v: string | number | null | undefined, opts: { signed?: boolean } = {}): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return "—";
  const body = Math.abs(n).toLocaleString("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const sign = n < 0 ? "−" : opts.signed && n > 0 ? "+" : "";
  return `${sign}AUD $${body}`;
}
