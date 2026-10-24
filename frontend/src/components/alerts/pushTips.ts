/** R3.9: plain-language fixes for the push test card. */
export function deniedHelp(): string {
  const ua = navigator.userAgent;
  if (/iPhone|iPad/.test(ua)) return "Open the iPhone Settings app → Notifications → brokAId → turn on Allow Notifications. Then come back here.";
  if (/Android/.test(ua)) return "In Chrome tap ⋮ → Settings → Site settings → Notifications, find this site and choose Allow. Then reload.";
  if (/Edg\//.test(ua)) return "Click the lock icon left of the address bar → Permissions for this site → Notifications → Allow. Then reload.";
  if (/Firefox/.test(ua)) return "Click the icon left of the address bar → clear the Notifications block (Blocked Temporarily / Blocked) → reload and allow.";
  if (/Chrome/.test(ua)) return "Click the icon left of the address bar → Site settings → Notifications → Allow. Then reload the page.";
  if (/Safari/.test(ua)) return "Safari → Settings → Websites → Notifications → find this site → Allow. Then reload.";
  return "Allow notifications for this site in your browser settings, then reload.";
}

export const IOS_INSTALL = "Add brokAId to your Home Screen first (Share → Add to Home Screen), then open it from there. iPhone only allows alerts for apps opened from the Home Screen.";

export const NOT_ARRIVED_TIPS = [
  "Focus or Do Not Disturb is on. Turn it off, or allow this browser or app during Focus.",
  "The operating system blocks alerts for this browser. On Mac: System Settings → Notifications → your browser → Allow. On Windows: Settings → System → Notifications → your browser → On.",
  "On iPhone, alerts only work in the Home Screen app (Share → Add to Home Screen), with Notifications allowed in Settings → Notifications → brokAId.",
  "Battery saver or Low Power Mode can delay alerts. Turn it off and try again.",
  "If the browser was fully closed, some systems hold alerts until it's opened again.",
];
