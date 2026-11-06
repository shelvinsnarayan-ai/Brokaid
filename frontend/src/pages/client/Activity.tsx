import { useEffect, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { Copy, cget, Skel } from "./api";

export interface Item { type: string; at: string; date: string; title: string; subtitle: string; amount?: string; state?: string; crn?: string; href: string }
interface Due { payable_id: string; policy: string; amount: string; due: string; crn: string; mocked: boolean; message?: string;
  bpay?: { biller_code: string; reference: string } | null; pay_href: string | null }
interface Side {
  total_due: string | null; last_received: { amount: string; date: string; policy: string } | null;
  next_renewal: { date: string; policy: string; href: string } | null; open_claims: { count: number; href: string };
}
const CHIPS = [["all", "All"], ["payments", "Payments"], ["claims", "Claims"], ["changes", "Changes"], ["renewals", "Renewals"], ["documents", "Documents"]];

export function Feed({ items, testid }: { items: Item[]; testid: string }) {
  const days: [string, Item[]][] = [];
  items.forEach((i) => { const last = days[days.length - 1]; if (last && last[0] === i.date) last[1].push(i); else days.push([i.date, [i]]); });
  if (!items.length) return <p className="mer-empty" data-testid={`${testid}-empty`}>Nothing here yet.</p>;
  return (
    <div data-testid={testid}>
      {days.map(([d, list]) => (
        <div key={d}>
          <p className="mer-date">{d}</p>
          <div className="mer-card">
            {list.map((i, k) => (
              <Link key={k} to={i.href} className="mer-row" data-testid={`${testid}-item-${i.type}`}>
                <span className="mer-row__main"><p className="mer-row__t">{i.title}</p><p className="mer-row__s">{i.subtitle}</p></span>
                {i.amount ? <span className="mer-fig"><p className="mer-fig__n">{i.amount}</p><p className="mer-fig__l">{i.state}</p></span> : null}
              </Link>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function Activity() {
  const [params, setParams] = useSearchParams();
  const f = params.get("f") || "all";
  const [items, setItems] = useState<Item[] | null>(null);
  const [due, setDue] = useState<Due[]>([]);
  const [side, setSide] = useState<Side | null>(null);
  const [next, setNext] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setItems(null); setErr(null);
    cget<{ items: Item[]; due: Due[]; panel: Side | null; next_cursor: number | null }>(`c/activity?f=${f}`)
      .then((d) => { setItems(d.items); setDue(d.due); setSide(d.panel); setNext(d.next_cursor); }).catch((e) => setErr(e.message));
  }, [f]);
  const { hash } = useLocation();
  useEffect(() => {
    if (hash.startsWith("#due-") && due.length) document.getElementById(hash.slice(1))?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [hash, due]);
  const more = () => next !== null && cget<{ items: Item[]; next_cursor: number | null }>(`c/activity?f=${f}&cursor=${next}`)
    .then((d) => { setItems((x) => [...(x || []), ...d.items]); setNext(d.next_cursor); });
  return (
    <div data-testid="mer-activity">
      <header className="mer-head"><h1 className="mer-head__name mer-h">Activity</h1></header>
      <div className="mer-act">
      <PaymentsPanel due={due} side={side} loading={items === null} />
      <section className="mer-sec mer-act__feed" data-testid="mer-activity-feed-col">
        <div className="mer-chips" role="tablist" aria-label="Filter activity">
          {CHIPS.map(([k, l]) => (
            <button key={k} type="button" role="tab" aria-selected={f === k} className={`mer-chip${f === k ? " mer-chip--on" : ""}`}
              onClick={() => setParams(k === "all" ? {} : { f: k })} data-testid={`mer-chip-${k}`}>{l}</button>
          ))}
        </div>
        {err ? <p className="mer-empty" role="alert">{err}</p> : items === null ? <Skel /> : <Feed items={items} testid="mer-feed" />}
        {next !== null ? <button type="button" className="mer-btn mer-btn--ghost" style={{ margin: "14px auto", display: "flex" }} onClick={more} data-testid="mer-feed-more">Show more</button> : null}
      </section>
      </div>
    </div>
  );
}

function DueItem({ d }: { d: Due }) {
  return (
    <div className={`mer-due${useLocation().hash === `#due-${d.payable_id}` ? " mer-due--hl" : ""}`} id={`due-${d.payable_id}`}
      data-testid={`mer-due-${d.payable_id}`}>
      <p style={{ margin: 0, fontWeight: 700 }}>{d.amount}{d.due ? <span className="mer-row__s"> · due {d.due}</span> : null}</p>
      <p className="mer-row__s">{d.policy}</p>
      {d.bpay ? (
        <div className="mer-due__pay">
          <span className="mer-row__s">BPAY biller {d.bpay.biller_code} · ref {d.bpay.reference}{d.mocked ? " (MOCKED)" : ""}</span>
          <span style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <Copy text={d.bpay.biller_code} label="biller code" testid={`mer-copy-biller-${d.payable_id}`} />
            <Copy text={d.bpay.reference} label="reference" testid={`mer-copy-crn-${d.payable_id}`} />
          </span>
        </div>
      ) : <p className="mer-row__s" data-testid={`mer-due-msg-${d.payable_id}`}>{d.message || "Your broker will send payment details."}</p>}
      {d.pay_href ? <Link to={d.pay_href} className="mer-btn" style={{ marginTop: 10 }} data-testid={`mer-pay-${d.payable_id}`}>I've paid this</Link> : null}
    </div>
  );
}

function PaymentsPanel({ due, side, loading }: { due: Due[]; side: Side | null; loading: boolean }) {
  return (
    <aside className="mer-act__side" aria-labelledby="pay-due-h" data-testid="mer-payments-panel">
      <div className="mer-sec">
        <h2 id="pay-due-h" className="mer-sec__title mer-desk">Payments due</h2>
        {loading ? <div className="mer-desk"><Skel h={90} n={1} /></div> : due.length ? (
          <>
            {due.map((d) => <DueItem key={d.payable_id} d={d} />)}
            {side?.total_due ? <div className="mer-desk"><p className="mer-total" data-testid="mer-total-due">Total due <strong>{side.total_due}</strong></p></div> : null}
          </>
        ) : (
          <div className="mer-card mer-desk" style={{ padding: 14 }} data-testid="mer-payments-empty">
            <p style={{ margin: 0, fontWeight: 600 }}>Nothing to pay right now</p>
            {side?.last_received ? <p className="mer-row__s" data-testid="mer-last-received">Last payment received: {side.last_received.amount} on {side.last_received.date} · {side.last_received.policy}</p> : null}
          </div>
        )}
        {side ? (
          <div className="mer-card mer-desk" style={{ marginTop: 14 }} data-testid="mer-coming-up">
            <p className="mer-group" style={{ padding: "12px 14px 0", margin: 0 }}>Coming up</p>
            {side.next_renewal ? (
              <Link to={side.next_renewal.href} className="mer-row" data-testid="mer-coming-renewal">
                <span className="mer-row__main"><p className="mer-row__t">Next renewal · {side.next_renewal.date}</p><p className="mer-row__s">{side.next_renewal.policy}</p></span>›
              </Link>
            ) : null}
            <Link to={side.open_claims.href} className="mer-row" data-testid="mer-coming-claims">
              <span className="mer-row__main"><p className="mer-row__t">{side.open_claims.count} open claim{side.open_claims.count === 1 ? "" : "s"}</p></span>›
            </Link>
          </div>
        ) : null}
      </div>
    </aside>
  );
}
