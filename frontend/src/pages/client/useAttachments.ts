/** R3.5 — draft attachments for one claim message: add (checked), upload with progress, single retry, remove. */
import { useRef, useState } from "react";
import { check, kindOf, upload, type Item, type Limits } from "./attachUpload";

const API = (import.meta as any).env.VITE_API_BASE || "";
const newId = () => (crypto as any).randomUUID?.() || "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c: any) =>
  (c ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (c / 4)))).toString(16));

export function useAttachments(claimId: string, limits: Limits | undefined) {
  const [items, setItems] = useState<Item[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [msgId, setMsgId] = useState(newId);
  const ref = useRef<Item[]>([]);
  const set = (fn: (xs: Item[]) => Item[]) => setItems((xs) => (ref.current = fn(xs)));
  const patch = (key: string, p: Partial<Item>) => set((xs) => xs.map((x) => (x.key === key ? { ...x, ...p } : x)));

  const start = (it: Item) => {
    const { xhr, done } = upload(`${API}/api/c/claims/${claimId}/thread/attachments`, it.file, msgId, (p) => patch(it.key, { progress: p }));
    patch(it.key, { status: "uploading", progress: 0, xhr, tries: it.tries + 1, error: undefined });
    done.then((f) => patch(it.key, { status: "done", progress: 100, file_id: f.file_id, has_preview: f.has_preview }))
      .catch((e) => { if (e.status !== -1) patch(it.key, { status: "error", error: e.message, retryable: e.status === 0 || e.status >= 500 }); });
  };

  const add = (files: FileList | null) => {
    if (!files || !limits) return;
    const { ok, errors: errs } = check(Array.from(files), ref.current, limits);
    setErrors(errs);
    const fresh: Item[] = ok.map((f) => ({ key: newId(), file: f, kind: kindOf(f)!, url: URL.createObjectURL(f), status: "uploading", progress: 0, tries: 0 }));
    set((xs) => [...xs, ...fresh]);
    fresh.forEach(start);
  };

  const remove = (key: string) => {
    const it = ref.current.find((x) => x.key === key);
    if (!it) return;
    it.xhr?.abort();
    if (it.file_id) void fetch(`${API}/api/c/claims/${claimId}/thread/attachments/${it.file_id}`, { method: "DELETE", credentials: "include" });
    URL.revokeObjectURL(it.url);
    set((xs) => xs.filter((x) => x.key !== key));
    setErrors([]);
  };

  const retry = (key: string) => { const it = ref.current.find((x) => x.key === key); if (it && it.tries < 2) start(it); };

  const reset = () => { ref.current.forEach((x) => URL.revokeObjectURL(x.url)); set(() => []); setErrors([]); setMsgId(newId()); };

  const busy = items.some((x) => x.status === "uploading");
  const blocked = items.some((x) => x.status === "error");
  const fileIds = items.filter((x) => x.status === "done").map((x) => x.file_id!);
  return { items, errors, msgId, add, remove, retry, reset, busy, blocked, fileIds };
}
