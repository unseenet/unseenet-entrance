// Unseenet on the web: the client kept on this device (UN-RFC 0043 section 7.3).
//
// This worker only serves what the page has kept: the page downloads a version,
// checks every file against the SHA-256 in a signed post of unseenet.releases,
// stores it in the cache "unseenet-web-<version>" and names it in "unseenet-meta"
// /__active. Until then everything comes from the entrance, as without a worker.
// It is the same for every version, so the browser rarely has a new one to fetch:
// a different sw.js from the entrance is something the page warns about.
//
// Never kept: the tunnel (/onntp), what the entrance says about itself and others
// (/.well-known/), the API, the jump page and the client in a file.
const META = "unseenet-meta";
const NEVER = /^\/(onntp|\.well-known\/|api\/|unseenet-entrances\.html|unseenet-web\.html)/;

// The headers the entrance sends (server.py), given to kept files too.
const PAGE_CSP = "default-src 'self'; connect-src 'self' https: http: ws: wss:; " +
  "img-src 'self' data: blob:; style-src 'self'; script-src 'self' 'wasm-unsafe-eval'; " +
  "worker-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'";
const WORKER_CSP = "default-src 'self'; connect-src 'self' https: http: ws: wss:; " +
  "script-src 'self' 'unsafe-eval' 'wasm-unsafe-eval'; object-src 'none'; base-uri 'none'";
const TYPES = {
  html: "text/html; charset=utf-8", js: "text/javascript; charset=utf-8", mjs: "text/javascript; charset=utf-8",
  css: "text/css; charset=utf-8", json: "application/json", webmanifest: "application/manifest+json",
  wasm: "application/wasm", zip: "application/zip", whl: "application/zip", svg: "image/svg+xml",
  png: "image/png", txt: "text/plain; charset=utf-8",
};

let active;                 // {version, cache, signed} or null; undefined: not read yet
async function current() {
  if (active === undefined) {
    try {
      const r = await (await caches.open(META)).match("/__active");
      active = r ? await r.json() : null;
    } catch { active = null; }
  }
  return active;
}

function headersFor(path) {
  const ext = (path.match(/\.([a-z0-9]+)$/) || [, "html"])[1];
  const h = { "Content-Type": TYPES[ext] || "application/octet-stream", "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer", "Cross-Origin-Opener-Policy": "same-origin" };
  if (ext === "html") h["Content-Security-Policy"] = PAGE_CSP;
  if (path === "/worker.js") h["Content-Security-Policy"] = WORKER_CSP;
  return h;
}

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (e) => {
  // caches of the first app (one per build, named by a hash) are no longer used
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => /^unseenet-[0-9a-f]{16}$/.test(k)).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("message", (e) => {
  if (e.data && e.data.type === "active-changed") active = undefined;
});

self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (u.origin !== location.origin || e.request.method !== "GET" || NEVER.test(u.pathname)) return;
  if (u.searchParams.has("unseenet-fetch")) return;      // the page fetching from the entrance itself
  e.respondWith((async () => {
    const a = await current();
    if (a) {
      const path = u.pathname === "/" ? "/index.html" : u.pathname;
      const hit = await (await caches.open(a.cache)).match(path);
      if (hit) return new Response(hit.body, { status: 200, headers: headersFor(path) });
    }
    return fetch(e.request);
  })());
});
