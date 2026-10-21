// Unseenet on the web: the client kept on this device, so it installs to the home
// screen and starts at once (about 15 MB is fetched only once per version).
// VERSION is set by tools/build_web.py from the files it serves; a new build is
// a new version, and the old one is removed.
//
// Never cached: the tunnel (/onntp), what the entrance says about itself and
// other entrances (/.well-known/), the jump page and the client in a file.
const VERSION = "__VERSION__";
const CACHE = "unseenet-" + VERSION;
const SHELL = ["/", "/shim.js", "/worker.js", "/entrance.css", "/static/app.css", "/static/app.js",
  "/static/icon.svg", "/static/icon-ink.svg", "/app/manifest.webmanifest", "/app/icon-192.png"];
const KEEP = /^\/(pyodide|py|static|app)\//;
const NEVER = /^\/(onntp|\.well-known\/|api\/|unseenet-entrances\.html|unseenet-web\.html)/;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith("unseenet-") && k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (u.origin !== location.origin || e.request.method !== "GET" || NEVER.test(u.pathname)) return;
  e.respondWith((async () => {
    const c = await caches.open(CACHE);
    const hit = await c.match(e.request, { ignoreSearch: true });
    if (hit) return hit;
    const r = await fetch(e.request);
    if (r.ok && (KEEP.test(u.pathname) || SHELL.includes(u.pathname))) c.put(e.request, r.clone());
    return r;
  })());
});
