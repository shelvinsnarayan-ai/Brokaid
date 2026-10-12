/** R0.6 — thing card; every value comes from /api/c/things (thing + schedule_items). */
import { Link } from "react-router-dom";

export interface Thing {
  thing_id: string; group: string; name: string; status: string; status_sentence: string;
  for_sentence: string; identifiers: Record<string, string>; numbers: { label: string; value: string }[];
  policy_ids: string[]; product: string | null;
}

const GROUP_LABEL: Record<string, string> = {
  vehicle: "Vehicles", place: "Places", business: "Business", person: "People",
  travel: "Travel", not_covered: "Not covered",
};

export function ThingCard({ t }: { t: Thing }) {
  const ident = t.identifiers.rego || t.identifiers.address || "";
  const pid = t.policy_ids[0];
  return (
    <article className={`brokaid-thing brokaid-thing--${t.status}`} data-testid={`thing-card-${t.thing_id}`}>
      <p className="brokaid-thing__status" data-testid={`thing-status-${t.thing_id}`}>{t.status_sentence}</p>
      <h3 className="brokaid-thing__name">{t.name}{ident && t.group === "vehicle" ? ` · ${ident}` : ""}</h3>
      <p className="brokaid-thing__for" data-testid={`thing-for-${t.thing_id}`}>{t.for_sentence}</p>
      {t.numbers.length > 0 && (
        <dl className="brokaid-thing__numbers">
          {t.numbers.map((n) => (
            <div key={n.label}><dt>{n.label}</dt><dd data-testid={`thing-num-${t.thing_id}-${n.label.toLowerCase().replace(/\s+/g, "-")}`}>{n.value}</dd></div>
          ))}
        </dl>
      )}
      {t.status === "covered" && pid && (
        <div className="brokaid-thing__actions">
          <Link to={`/c/cover/${pid}/certificate/new`}>Certificate</Link>
          <Link to={`/c/cover/${pid}/request-change`}>Change</Link>
          <Link to="/c/claims/new">Claim</Link>
        </div>
      )}
      {t.status !== "covered" && (
        <div className="brokaid-thing__actions"><Link to="/c/ask">Ask your broker for a quote</Link></div>
      )}
    </article>
  );
}

export function ThingGroups({ things }: { things: Thing[] }) {
  const groups = Array.from(new Set(things.map((t) => t.group)));
  return (
    <section className="brokaid-things" data-testid="home-things">
      {groups.map((g) => (
        <div key={g} data-testid={`things-group-${g}`}>
          <h2 className="brokaid-things__group">{GROUP_LABEL[g] || g}</h2>
          {things.filter((t) => t.group === g).map((t) => <ThingCard key={t.thing_id} t={t} />)}
        </div>
      ))}
    </section>
  );
}
