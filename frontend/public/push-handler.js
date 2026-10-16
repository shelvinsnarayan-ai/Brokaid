/* R0.13 Phase C — Web Push handlers, imported into the Workbox service worker (importScripts).
   Caching and auto-update stay with the generated SW; this file only shows and opens alerts. */
self.addEventListener("push", (event) => {
  let d = {};
  try {
    d = event.data ? event.data.json() : {};
  } catch (e) {
    d = { title: "brokAId", body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.registration.showNotification(d.title || "brokAId", {
      body: d.body || "",
      icon: "/pwa/pwa-192.png",
      badge: "/pwa/pwa-192.png",
      tag: d.tag,
      data: { link: d.link || "/c/alerts" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL((event.notification.data && event.notification.data.link) || "/", self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      const tab = list.find((c) => c.url.startsWith(self.location.origin));
      if (tab) {
        return tab.focus().then((w) => (w && "navigate" in w ? w.navigate(url).catch(() => w) : w))
          .catch(() => self.clients.openWindow(url));
      }
      return self.clients.openWindow(url);
    })
  );
});
