import { useEffect } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { Ico, Skel, useC } from "./api";
import { Feed, type Item } from "./Activity";
import type { Fig } from "./Home";
import { Explain } from "./Explain";

interface Page {
  policy_id: string; title: string; cover_type: string; figure: Fig | null; insurer: string | null; status_text: string; current: boolean;
  covered: string[]; excess: string | null; summary?: string | null; renewal: { renewal_id: string; text: string } | null;
  vehicles: { rego: string; title: string; use?: string; figure: Fig | null }[]; activity: Item[];
  previous: { policy_id: string; product: string; period: string; status: string }[];
  renewal_section: { state: string; text: string; renews_on: string; action: { label: string; href: string } | null } | null;
  key_numbers: { label: string; value: string; explain: string }[];
}

export default function PolicyPage() {
  const { policy_id } = useParams();
  const { data, err } = useC<Page>(`c/policy/${policy_id}`);
  const { hash } = useLocation();
  useEffect(() => { if (data && hash) document.getElementById(hash.slice(1))?.scrollIntoView({ behavior: "smooth" }); }, [data, hash]);
  if (err) return <p className="mer-empty" role="alert" style={{ margin: 16 }} data-testid="mer-policy-error">{err}</p>;
  if (!data) return <div style={{ padding: 16 }}><Skel h={120} n={1} /><Skel /></div>;
  const base = `/c/cover/${data.policy_id}`;
  return (
    <div className="mer-col--wide" data-testid="mer-policy">
      <div>
        <div className="mer-hero" data-testid="mer-policy-hero">
          <Link to="/c" style={{ color: "#fff", opacity: .8, fontSize: 13, textDecoration: "none" }} data-testid="mer-policy-back">‹ Home</Link>
          <h1 className="mer-h" style={{ margin: "8px 0 0", fontSize: 20 }}>{data.title}</h1>
          <p style={{ margin: "2px 0 0", opacity: .8, fontSize: 13 }}>{data.cover_type}{data.insurer ? ` · ${data.insurer}` : ""}</p>
          {data.figure ? <p className="mer-hero__fig" data-testid="mer-policy-figure">{data.figure.text} <span style={{ fontSize: 13, fontWeight: 500, opacity: .8 }}>{data.figure.label}</span></p> : null}
          <p style={{ margin: "8px 0 0", fontSize: 13 }} data-testid="mer-policy-status">{data.status_text}</p>
        </div>
        {data.renewal_section ? (
          <section className="mer-sec" id="renewal" data-testid="mer-policy-renewal-section">
            <h2 className="mer-sec__title"><span>Renewal <Explain k="renewal_date" /></span></h2>
            <div className="mer-card mer-renew">
              <p className="mer-renew__t" data-testid="mer-policy-renewal-text">{data.renewal_section.text}</p>
              {data.renewal_section.state !== "not_started" && data.renewal_section.state !== "renewed"
                ? <p className="mer-renew__s">Renews {data.renewal_section.renews_on}</p> : null}
              {data.renewal_section.action ? <Link to={data.renewal_section.action.href} className="mer-btn"
                data-testid="mer-policy-renewal-action">{data.renewal_section.action.label}</Link> : null}
            </div>
          </section>
        ) : null}
        <nav className="mer-quick" aria-label="Policy actions" style={{ marginTop: 14 }}>
          <Link to={`/c/claims/new?policy=${data.policy_id}`} data-testid="mer-quick-claim"><span className="c"><Ico name="claim" /></span>Make a claim</Link>
          <Link to={`${base}/request-change`} data-testid="mer-quick-change"><span className="c"><Ico name="change" /></span>Change cover</Link>
          <Link to={`${base}/certificate/new`} data-testid="mer-quick-certificate"><span className="c"><Ico name="cert" /></span>Certificate</Link>
          <Link to="/c/documents" data-testid="mer-quick-documents"><span className="c"><Ico name="doc" /></span>Documents</Link>
        </nav>
        <section className="mer-sec" data-testid="mer-policy-covered">
          <h2 className="mer-sec__title">What you're covered for</h2>
          <div className="mer-card">
            {data.covered.length ? <ul className="mer-list">{data.covered.map((c) => <li key={c}>{c}</li>)}</ul>
              : <p style={{ padding: 14, margin: 0, fontSize: 14 }}>{data.summary || "Your schedule has the full details — ask your broker if anything's unclear."}</p>}
          </div>
          {data.key_numbers.length ? (
            <div className="mer-card" style={{ marginTop: 10 }} data-testid="mer-policy-key-numbers">
              {data.key_numbers.map((k, i) => (
                <div key={k.label} className="mer-kv" data-testid={`mer-policy-kv-${i}`}>
                  <span>{k.label}<Explain k={k.explain} /></span><b>{k.value}</b>
                </div>
              ))}
            </div>
          ) : null}
          {data.vehicles.map((v) => (
            <div key={v.rego} id={`v-${v.rego}`} className="mer-card" style={{ marginTop: 10 }} data-testid={`mer-vehicle-${v.rego}`}>
              <div className="mer-row"><span className="mer-ico"><Ico name="car" /></span>
                <span className="mer-row__main"><p className="mer-row__t">{v.title} · {v.rego}</p><p className="mer-row__s">{v.use || "Insured vehicle"}</p></span>
                <span className="mer-fig">{v.figure ? <><p className="mer-fig__n">{v.figure.text}</p><p className="mer-fig__l">{v.figure.label}</p></> : <p className="mer-fig__l">View details</p>}</span>
              </div>
            </div>
          ))}
        </section>
      </div>
      <div>
        <section className="mer-sec">
          <h2 className="mer-sec__title">Activity</h2>
          <Feed items={data.activity} testid="mer-policy-feed" />
        </section>
        <section className="mer-sec" data-testid="mer-policy-previous">
          <h2 className="mer-sec__title">Previous terms</h2>
          {data.previous.length ? <div className="mer-card">{data.previous.map((p) => (
            <div key={p.policy_id} className="mer-row"><span className="mer-row__main"><p className="mer-row__t">{p.product}</p>
              <p className="mer-row__s">{p.period} · {p.status}</p></span></div>))}</div>
            : <p className="mer-empty">No previous terms.</p>}
        </section>
      </div>
    </div>
  );
}
