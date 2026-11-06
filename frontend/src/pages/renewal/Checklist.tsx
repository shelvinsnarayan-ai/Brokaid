import { useState } from "react";

interface Item { key: string; kind: string; question: string; current?: any }

export function RenewalChecklist({ items, busy, onSubmit }: { items: Item[]; busy: boolean; onSubmit: (a: Record<string, any>) => void }) {
  const [a, setA] = useState<Record<string, any>>({ vehicles: { confirmed: true, remove: [] }, address: { confirmed: true }, claims: { any: false } });
  const set = (k: string, v: any) => setA({ ...a, [k]: { ...(a[k] || {}), ...v } });
  const yesNo = (k: string, field: string, yes: boolean) => (
    <span className="brokaid-form__row">
      <label><input type="radio" checked={a[k]?.[field] === yes} onChange={() => set(k, { [field]: yes })} data-testid={`checklist-${k}-yes`} /> Yes</label>
      <label><input type="radio" checked={a[k]?.[field] === !yes} onChange={() => set(k, { [field]: !yes })} data-testid={`checklist-${k}-no`} /> No</label>
    </span>
  );
  return (
    <form className="rn-c-page" data-testid="client-renewal-checklist" onSubmit={(e) => { e.preventDefault(); onSubmit(a); }}>
      {items.map((it) => (
        <fieldset key={it.key} className="rn-c-q" data-testid={`checklist-item-${it.key}`}>
          <legend><b>{it.question}</b></legend>
          {it.kind === "vehicles" ? (
            <>
              {it.current.map((v: any) => (
                <label key={v.rego}><input type="checkbox" checked={!a.vehicles.remove.includes(v.rego)} data-testid={`checklist-vehicle-${v.rego}`}
                  onChange={(e) => set("vehicles", { confirmed: false, remove: e.target.checked ? a.vehicles.remove.filter((x: string) => x !== v.rego) : [...a.vehicles.remove, v.rego] })} />
                  {" "}{v.rego} {v.label}</label>
              ))}
              <span className="rn-c-warn">Untick a vehicle you no longer have. New vehicles: tell us under "anything else".</span>
            </>
          ) : null}
          {it.kind === "address" ? (
            <>
              <span>{it.current}</span>{yesNo("address", "confirmed", true)}
              {a.address.confirmed === false ? <input placeholder="New address" onChange={(e) => set("address", { new_address: e.target.value })} data-testid="checklist-address-new" /> : null}
            </>
          ) : null}
          {it.kind === "values" ? (
            <>
              {it.current.map((v: any) => {
                const cur = a.values?.items?.[v.id] || { confirmed: true };
                const put = (x: any) => set("values", { items: { ...(a.values?.items || {}), [v.id]: { ...cur, ...x } } });
                return (
                  <span key={v.id} className="rn-c-value" data-testid={`checklist-value-${v.id}`}>
                    <span>{v.label}: <b>${Number(v.current).toLocaleString("en-AU")}</b></span>
                    <label><input type="radio" checked={cur.confirmed} onChange={() => put({ confirmed: true })} data-testid={`checklist-value-${v.id}-ok`} /> Still right</label>
                    <label><input type="radio" checked={!cur.confirmed} onChange={() => put({ confirmed: false })} data-testid={`checklist-value-${v.id}-change`} /> Change it</label>
                    {!cur.confirmed ? <input inputMode="numeric" placeholder="New amount (A$)" onChange={(e) => put({ requested: e.target.value })}
                      data-testid={`checklist-value-${v.id}-amount`} /> : null}
                  </span>
                );
              })}
              <span className="rn-c-warn">Changes are requests: your broker checks them with the insurer before anything changes.</span>
            </>
          ) : null}
          {it.kind === "money" ? <input inputMode="numeric" placeholder="e.g. 1,200,000" onChange={(e) => set("turnover", { value: e.target.value })} data-testid="checklist-turnover" /> : null}
          {it.kind === "claims" ? (
            <>
              {yesNo("claims", "any", true)}
              {a.claims.any ? <textarea placeholder="What happened?" onChange={(e) => set("claims", { details: e.target.value })} data-testid="checklist-claims-details" /> : null}
            </>
          ) : null}
          {it.kind === "text" ? <textarea placeholder="Optional" onChange={(e) => set("changes", { text: e.target.value })} data-testid="checklist-changes" /> : null}
        </fieldset>
      ))}
      <button className="brokaid-cta" type="submit" disabled={busy} data-testid="checklist-submit">Send my details</button>
    </form>
  );
}
