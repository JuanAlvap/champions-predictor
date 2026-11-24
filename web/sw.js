// Service worker: la app abre sin internet con los últimos datos descargados.
const SHELL = "shell-v1";
const DATA = "data-v1";
const SHELL_FILES = ["./", "index.html", "styles.css", "app.js", "manifest.webmanifest",
  "icons/icon-192.png", "icons/apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((k) => ![SHELL, DATA].includes(k)).map((k) => caches.delete(k))
  )).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  if (url.hostname === "api.github.com") return; // siempre en vivo
  const isData = url.pathname.includes("/data/");
  if (isData || url.origin !== location.origin) {
    // Red primero; si no hay conexión, lo último guardado
    e.respondWith(fetch(e.request).then((res) => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(DATA).then((c) => c.put(e.request.url.split("?")[0], copy));
      }
      return res;
    }).catch(() => caches.match(e.request.url.split("?")[0])));
    return;
  }
  // App: caché primero, y se actualiza en segundo plano
  e.respondWith(caches.match(e.request).then((hit) => {
    const net = fetch(e.request).then((res) => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(SHELL).then((c) => c.put(e.request, copy));
      }
      return res;
    }).catch(() => hit);
    return hit || net;
  }));
});
