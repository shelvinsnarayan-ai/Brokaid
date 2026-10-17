const API = import.meta.env.VITE_API_BASE || "";

export async function authCall<T = any>(path: string, body?: unknown, method = "POST"): Promise<T> {
  const res = await fetch(`${API}/api/${path}`, {
    method,
    credentials: "include",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : "Something went wrong. Please try again.");
  }
  return data as T;
}

export async function signOut(): Promise<void> {
  await authCall("auth/logout").catch(() => undefined);
}
