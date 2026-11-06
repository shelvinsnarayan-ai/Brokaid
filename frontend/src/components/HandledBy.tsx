/** R3.15: "Handled by" card for client matters (claim, change, renewal). Dates only when the server has a real one. */
export type Handler = { name: string; role: string; initials: string; photo_url?: string | null; next_step?: string; by?: string; update_by?: string };

export function HandledBy({ h, testid = "handled-by", action }: { h?: Handler | null; testid?: string; action?: React.ReactNode }) {
  if (!h) return null;
  return (
    <section className="mer-sec" data-testid={testid}>
      <h2 className="mer-sec__title">Handled by</h2>
      <div className="mer-card"><div className="mer-row">
        {h.photo_url ? <img src={h.photo_url} alt="" className="mer-avatar" data-testid={`${testid}-photo`} />
          : <span className="mer-avatar" aria-hidden="true" data-testid={`${testid}-initials`}>{h.initials}</span>}
        <span className="mer-row__main">
          <p className="mer-row__t" data-testid={`${testid}-name`}>{h.name}</p>
          <p className="mer-row__s">{h.role}</p>
          {h.next_step ? (
            <p className="mer-row__s" data-testid={`${testid}-next`}>
              Next step: {h.next_step}{h.by ? ` by ${h.by}` : ""}{!h.by && h.update_by ? `. We'll update you by ${h.update_by}` : ""}
            </p>
          ) : null}
        </span>
        {action}
      </div></div>
    </section>
  );
}
