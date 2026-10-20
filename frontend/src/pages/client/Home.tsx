import { Link, useSearchParams } from "react-router-dom";
import { Bell } from "../../components/alerts/Bell";
import { Ico, Skel, useC } from "./api";

export interface Fig { text: string; label: string; kind: string }
interface Row { policy_id: string; title: string; subtitle: string; badge: string | null; figure: Fig | null; icon: string; anchor: string | null }
interface HomeData {
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
