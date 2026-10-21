import { Link, useSearchParams } from "react-router-dom";
import { Bell } from "../../components/alerts/Bell";
import { Ico, Skel, useC } from "./api";

export interface Fig { text: string; label: string; kind: string }
interface Row { policy_id: string; title: string; subtitle: string; badge: string | null; figure: Fig | null; icon: string; anchor: string | null }
interface RemItem { payable_id: string; amount: string; policy: string; due_text: string; overdue: boolean; href: string | null }
type Reminder = { combined: false; item: RemItem; can_pay: boolean } | { combined: true; count: number; total: string; overdue: boolean; href: string; can_pay: boolean };
interface HomeData {
  reminder?: Reminder | null;
  greeting: string; client_name: string | null; broker: { name: string; email?: string | null; phone?: string | null };
  attention: { kind: string; title: string; subtitle: string; href: string }[];
  cover: { key: string; label: string; rows: Row[] }[];
}

export default function Home() {
  const { data, err } = useC<HomeData>("c/home");
  const [params] = useSearchParams();
  const pick = params.get("pick");
  return (
    <div data-testid="mer-home">
      <header className="mer-head">
        <div className="mer-head__top">
          <div>
            <p className="mer-head__greet" data-testid="mer-greeting">{data?.greeting || "Hello"}</p>
            <h1 className="mer-head__name mer-h" data-testid="mer-client-name">{data ? data.client_name || "there" : "…"}</h1>
          </div>
          <Bell base="/c" />
        </div>
        <Link to="/c/messages/broker" className="mer-head__broker" data-testid="mer-broker-contact">
          <Ico name="chat" /> {data?.broker.name || "Your broker"}
        </Link>
      </header>
      {pick ? <p className="mer-banner" data-testid="mer-pick-hint">Choose the policy{pick === "certificate" ? " for your certificate" : " to change"} below.</p> : null}
      <section className="mer-sec" aria-labelledby="att-h">
        <h2 id="att-h" className="mer-sec__title">Needs your attention</h2>
        {data?.reminder ? <PayReminder r={data.reminder} /> : null}
        {!data ? <Skel h={70} n={1} /> : data.attention.length ? (
          <div className="mer-strip" data-testid="mer-attention">
            {data.attention.map((a, i) => (
              <Link key={i} to={a.href} className="mer-att" data-testid={`mer-attention-${a.kind}`}>
                <p className="mer-att__t">{a.title}</p><p className="mer-att__s">{a.subtitle}</p>
              </Link>
            ))}
          </div>
        ) : <p className="mer-empty" data-testid="mer-attention-empty">You're all up to date.</p>}
      </section>
      <section className="mer-sec" aria-labelledby="cover-h">
        <h2 id="cover-h" className="mer-sec__title">Your cover</h2>
        {err ? <p className="mer-empty" role="alert">{err}</p> : !data ? <Skel /> : data.cover.length === 0 ? (
          <p className="mer-empty" data-testid="mer-cover-empty">No current policies yet. Your broker will add them here.</p>
        ) : data.cover.map((g) => (
          <div key={g.key} data-testid={`mer-group-${g.key}`}>
            <p className="mer-group">{g.label}</p>
            <div className="mer-card">{g.rows.map((r, i) => <CoverRow key={`${r.policy_id}-${i}`} r={r} pick={pick} />)}</div>
          </div>
        ))}
      </section>
    </div>
  );
}

function CoverRow({ r, pick }: { r: Row; pick: string | null }) {
  const to = pick === "change" ? `/c/cover/${r.policy_id}/request-change` : pick === "certificate" ? `/c/cover/${r.policy_id}/certificate/new`
    : `/c/policy/${r.policy_id}${r.anchor ? `#v-${r.anchor}` : ""}`;
  return (
    <Link to={to} className="mer-row" data-testid={`mer-cover-row-${r.policy_id}${r.anchor ? `-${r.anchor}` : ""}`}>
      <span className="mer-ico"><Ico name={r.icon} /></span>
      <span className="mer-row__main">
        <p className="mer-row__t">{r.title}</p>
        <p className="mer-row__s">{r.subtitle}</p>
        {r.badge ? <span className="mer-badge">{r.badge}</span> : null}
      </span>
      <span className="mer-fig">
        {r.figure ? <><p className="mer-fig__n">{r.figure.text}</p><p className="mer-fig__l">{r.figure.label}</p></>
          : <p className="mer-fig__l">View details</p>}
      </span>
    </Link>
  );
}

function PayReminder({ r }: { r: Reminder }) {
  if (r.combined) {
    return (
      <div className={`mer-remind${r.overdue ? " mer-remind--late" : ""}`} data-testid="mer-pay-reminder-combined">
        <p className="mer-remind__t">{r.count} payments due soon · {r.total} total</p>
        <p className="mer-row__s">{r.overdue ? "One or more was due already — " : ""}see the details and payment references in Activity.</p>
        <Link to={r.href} className="mer-btn" data-testid="mer-pay-reminder-view">{r.can_pay ? "Pay now" : "View payments"}</Link>
      </div>
    );
  }
  const i = r.item;
  return (
    <div className={`mer-remind${i.overdue ? " mer-remind--late" : ""}`} data-testid={`mer-pay-reminder-${i.payable_id}`}>
      <p className="mer-remind__t" data-testid="mer-pay-reminder-amount">{i.amount} {i.due_text}</p>
      <p className="mer-row__s">{i.policy}{i.overdue ? " · please pay when you can, or tell your broker if you already have" : ""}</p>
      {i.href ? <Link to={i.href} className="mer-btn" data-testid="mer-pay-reminder-pay">Pay now</Link>
        : <p className="mer-row__s" data-testid="mer-pay-reminder-noauth">Someone with payment authority for your business can pay this.</p>}
    </div>
  );
}
