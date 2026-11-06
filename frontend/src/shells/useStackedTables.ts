import { useEffect } from "react";

/** R3.13: label each table cell with its column heading so CSS can stack rows as cards on phones. */
export function useStackedTables(root: string, dep: unknown) {
  useEffect(() => {
    const el = document.querySelector(root);
    if (!el) return;
    const label = () => {
      el.querySelectorAll("table").forEach((t) => {
        const heads = [...t.querySelectorAll("thead th")].map((th) => (th as HTMLElement).innerText.trim());
        if (!heads.length) return;
        t.querySelectorAll("tbody tr").forEach((tr) => {
          [...tr.children].forEach((td, i) => {
            if (heads[i] && td.getAttribute("data-label") !== heads[i]) td.setAttribute("data-label", heads[i]);
          });
        });
      });
    };
    label();
    const obs = new MutationObserver(label);
    obs.observe(el, { childList: true, subtree: true });
    return () => obs.disconnect();
  }, [root, dep]);
}
