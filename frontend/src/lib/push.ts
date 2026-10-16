/** R0.13 Phase C — Web Push on this device (subscribe through the existing PWA service worker). */
const API = (import.meta as any).env.VITE_API_BASE || "";

export type PushSupport = "supported" | "ios_needs_install" | "unsupported";

export function pushSupport(): PushSupport {
  const ua = navigator.userAgent;
  const ios = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const standalone = window.matchMedia("(display-mode: standalone)").matches || (navigator as any).standalone === true;
  const ok = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  if (ios && !standalone) return "ios_needs_install";
  return ok ? "supported" : "unsupported";
}

function keyBytes(b64: string): Uint8Array {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  if (pushSupport() !== "supported") return null;
  const reg = await navigator.serviceWorker.getRegistration();
  return reg ? reg.pushManager.getSubscription() : null;
}

export async function turnOnPush(): Promise<"on" | "blocked" | "error"> {
  const perm = await Notification.requestPermission();
  if (perm !== "granted") return "blocked";
  try {
    const { key } = await (await fetch(`${API}/api/notifications/push-public-key`, { credentials: "include" })).json();
    const reg = await navigator.serviceWorker.ready;
    const sub = (await reg.pushManager.getSubscription())
      || (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) as BufferSource }));
    const r = await fetch(`${API}/api/notifications/push-subscriptions`, {
      method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...sub.toJSON(), user_agent: navigator.userAgent }),
    });
    return r.ok ? "on" : "error";
  } catch {
    return "error";
  }
}

export async function turnOffPush(): Promise<void> {
  const sub = await currentSubscription();
  if (!sub) return;
  await fetch(`${API}/api/notifications/push-subscriptions`, {
    method: "DELETE", credentials: "include", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ endpoint: sub.endpoint }),
  });
  await sub.unsubscribe();
}
