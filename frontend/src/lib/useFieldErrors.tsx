/** R3.15: inline field errors for client forms — validate on blur and on submit, focus the first error, keep entered data. */
import { useCallback, useState } from "react";

export type Rules<T> = Partial<Record<keyof T & string, (v: string, all: T) => string | null>>;

export function useFieldErrors<T extends Record<string, any>>(rules: Rules<T>, formId: string) {
  const [errors, setErrors] = useState<Record<string, string>>({});
  const check = useCallback((k: string, all: T) => {
    const msg = rules[k as keyof T & string]?.(String(all[k] ?? ""), all) || null;
    setErrors((e) => { const n = { ...e }; if (msg) n[k] = msg; else delete n[k]; return n; });
    return msg;
  }, [rules]);
  const focusFirst = (keys: string[]) => {
    const el = document.querySelector<HTMLElement>(`#${formId} [name="${keys[0]}"]`);
    el?.focus();
    el?.scrollIntoView?.({ block: "center" });
  };
  /** All rules; returns true when valid, otherwise shows every error and focuses the first. */
  const validateAll = (all: T) => {
    const e: Record<string, string> = {};
    for (const k of Object.keys(rules)) { const m = rules[k as keyof T & string]?.(String(all[k] ?? ""), all); if (m) e[k] = m; }
    setErrors(e);
    if (Object.keys(e).length) { window.setTimeout(() => focusFirst(Object.keys(e)), 0); return false; }
    return true;
  };
  /** Server field errors (from a 422) shown the same way. */
  const showServer = (e: Record<string, string>) => { setErrors(e); if (Object.keys(e).length) window.setTimeout(() => focusFirst(Object.keys(e)), 0); };
  const bind = (k: string, all: T) => ({
    name: k, "aria-invalid": errors[k] ? true : undefined, "aria-describedby": errors[k] ? `${formId}-${k}-err` : undefined,
    onBlur: () => check(k, all),
  });
  return { errors, bind, validateAll, showServer, check };
}

export function FieldError({ formId, field, errors }: { formId: string; field: string; errors: Record<string, string> }) {
  return errors[field] ? <p id={`${formId}-${field}-err`} className="brokaid-field-err" role="alert" data-testid={`${formId}-${field}-error`}>{errors[field]}</p> : null;
}
