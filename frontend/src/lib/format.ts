// AU display formats: dates dd/mm/yyyy (Australia/Sydney), money "A$2,550.00".

function toDate(v: string | number | Date | null | undefined): Date | null {
  if (v === null || v === undefined || v === "") return null;
  const d = typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) ? new Date(`${v}T12:00:00Z`) : new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}

// R3.15: every date is Sydney wall-clock (Australian format and timezone), whatever the device's timezone.
function syd(d: Date) {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-AU", { timeZone: "Australia/Sydney", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(d).map((x) => [x.type, x.value]));
  return { dd: p.day, mm: p.month, yyyy: p.year, hh: p.hour, mi: p.minute };
}
export function fmtDate(v: string | number | Date | null | undefined): string {
  const d = toDate(v);
  if (!d) return "—";
  const s = syd(d);
  return `${s.dd}/${s.mm}/${s.yyyy}`;
}

/** "3 days overdue" / "due today" / "due in 2 days" (whole Sydney days). */
export function overdueText(due: string | number | Date | null | undefined, now: Date = new Date()): string {
  const d = toDate(due);
  if (!d) return "—";
  const day = (x: Date) => { const s = syd(x); return Date.UTC(+s.yyyy, +s.mm - 1, +s.dd) / 86400000; };
  const n = day(now) - day(d);
  if (n === 0) return "due today";
  return n > 0 ? `${n} day${n === 1 ? "" : "s"} overdue` : `due in ${-n} day${n === -1 ? "" : "s"}`;
}

// Sydney wall-clock, labelled "AEST" by callers (demo-literal per Shelvin's ruling).
const SYD = { timeZone: "Australia/Sydney" } as const;
export function fmtSydDate(v: string | number | Date | null | undefined): string {
  const d = toDate(v);
  return d ? d.toLocaleDateString("en-AU", { ...SYD, day: "2-digit", month: "2-digit", year: "numeric" }) : "—";
}
export function fmtSydTime(v: string | number | Date | null | undefined): string {
  const d = toDate(v);
  return d ? d.toLocaleTimeString("en-AU", { ...SYD, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }) : "—";
}

export function fmtDateTime(v: string | number | Date | null | undefined): string {
  const d = toDate(v);
  if (!d) return "—";
  const s = syd(d);
  return `${s.dd}/${s.mm}/${s.yyyy} ${s.hh}:${s.mi}`;
}

export function fmtAud(v: string | number | null | undefined, opts: { signed?: boolean } = {}): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return "—";
  const body = Math.abs(n).toLocaleString("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const sign = n < 0 ? "−" : opts.signed && n > 0 ? "+" : "";
  return `${sign}A$${body}`;
}
