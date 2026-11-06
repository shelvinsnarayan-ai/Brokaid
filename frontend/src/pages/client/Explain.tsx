import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { EXPLAIN, NEXT } from "./explainers";

const ask = (t: string) => `/c/messages/broker?draft=${encodeURIComponent(`I have a question about the ${t.toLowerCase()} on my policy: `)}`;

export function Explain({ k }: { k: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const pop = useRef<HTMLSpanElement>(null);
  const e = EXPLAIN[k];
  useEffect(() => {
    if (!open) return;
    const close = (ev: MouseEvent | KeyboardEvent) => {
      const t = ev.target as Node;
      if (ev instanceof KeyboardEvent ? ev.key === "Escape" : !ref.current?.contains(t) && !pop.current?.contains(t)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, [open]);
  if (!e) return null;
  return (
    <span className="mer-ex" ref={ref}>
      <button type="button" className="mer-ex__btn" aria-label={`What is ${e.title}?`} aria-expanded={open}
        onClick={() => setOpen(!open)} data-testid={`explain-${k}`}>?</button>
      {open ? createPortal(
        <span ref={pop}>
          <span className="mer-ex__scrim" onClick={() => setOpen(false)} />
          <span className="mer-ex__pop" role="dialog" aria-label={e.title} data-testid={`explain-${k}-panel`}>
            <strong>{e.title}</strong>
            <span>{e.body}</span>
            <span className="mer-ex__foot">General information only, not advice about your situation.</span>
            <Link to={ask(e.title)} className="mer-btn mer-btn--ghost" data-testid={`explain-${k}-ask`}>Ask my broker</Link>
            <button type="button" className="mer-ex__close" onClick={() => setOpen(false)} data-testid={`explain-${k}-close`}>Close</button>
          </span>
        </span>, document.querySelector(".mer") || document.body,
      ) : null}
    </span>
  );
}

export function NextSteps({ flow }: { flow: keyof typeof NEXT }) {
  return (
    <aside className="mer-next" aria-label="What happens next" data-testid={`next-steps-${flow}`}>
      <p className="mer-next__t">What happens next</p>
      <ol>{NEXT[flow].map((s, i) => <li key={i}>{s}</li>)}</ol>
    </aside>
  );
}
