// Unseenet on the web: the graphical client (unseenet-gui) unchanged, with its
// backend in a Web Worker instead of on 127.0.0.1 (UN-RFC 0043 section 6).
// This script runs before the client's app.js and
//  - answers its fetch("/api/...", "/x/gui/...") and WebSocket("/api/events") from the worker,
//  - shows, always, that this is the open web (section 6.1),
//  - opens and saves the vault as a file (section 6.2), or - when the person
//    turns it on - keeps it on this device, still encrypted with their passphrase;
//  - installs the page as an app (a manifest and a service worker that keeps the
//    client on the device, so it starts at once from the home screen), keeping
//    only files a signed post in unseenet.releases names, and offers newer
//    versions from any entrance (section 7.3).
(() => {
  "use strict";
  // FILE: the client in a file (UN-RFC 0043 8.7) - set by the build, with the signed list
  const FILE = window.UNSEENET_FILE || null;
  let worker = null;
  const early = [];                // messages for the worker before it exists
  const toWorker = (msg, transfer = []) => (worker ? worker.postMessage(msg, transfer) : early.push([msg, transfer]));
  const pending = new Map();
  const sockets = new Set();
  let nextId = 1, booted = false, vaultDirty = false, bootText = "Starting ...";

  const call = (msg, transfer = []) => new Promise((resolve) => {
    const id = nextId++;
    pending.set(id, resolve);
    toWorker({ ...msg, id }, transfer);
  });

  function onWorker(e) {
    const m = typeof e.data === "string" ? JSON.parse(e.data) : e.data;
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
    if (m.type === "event") {
      const data = JSON.stringify(m.data);
      for (const s of sockets) s._message(data);
    } else if (m.type === "vault-changed") {
      if (kept) keepSoon(); else { vaultDirty = true; updateBar(); }
    } else if (m.type === "boot") {
      bootText = m.text; updateBoot();
    } else if (m.type === "ready") {
      booted = true; updateBoot();
    } else if (m.type === "fatal") {
      bootText = "The client could not start: " + m.text; updateBoot(true);
    }
  }

  function startWorker(w, init, transfer = []) {
    worker = w;
    worker.onmessage = onWorker;
    worker.onerror = (e) => { bootText = "The client could not start: " + (e.message || "the worker failed to load"); updateBoot(true); };
    worker.postMessage({ type: "init", ...init }, transfer);
    for (const [msg, tr] of early.splice(0)) worker.postMessage(msg, tr);
  }

  // --- the client in a file: the worker and its files come from this page --
  const fileBytes = async (name) => {
    const s = document.querySelector(`script[data-file="${name}"]`);
    if (!s) throw new Error("this file is incomplete: " + name);
    return (await realFetchEarly("data:application/octet-stream;base64," + s.textContent.trim())).arrayBuffer();
  };
  const realFetchEarly = window.fetch.bind(window);
  const blobURL = (buf, type) => URL.createObjectURL(new Blob([buf], { type }));
  let entrance = null;            // the entrance in use (FILE only)

  async function pickEntrance() {
    const verify = __m.entrances.verifyBundle;
    let list;
    try { list = FILE.owner ? verify(FILE.bundle, FILE.owner, FILE.group) : verify(FILE.bundle); }
    catch (err) { throw new Error("the list of entrances in this file cannot be trusted: " + err.message); }
    const candidates = list.entrances.filter((x) => x.url);
    const answers = await Promise.all(candidates.map(async (x) => {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), 8000);
      try { return (await realFetchEarly(new URL(".well-known/unseenet-entrance", x.url), { signal: ctl.signal })).ok ? x : null; }
      catch { return null; } finally { clearTimeout(t); }
    }));
    const ok = answers.filter(Boolean);
    if (!ok.length) throw new Error("no entrance in this file's list answers. Is this network blocking them? Try again later, or get a newer file.");
    return ok[Math.floor(Math.random() * ok.length)];
  }

  async function startFromFile() {
    bootText = "Looking for an entrance that answers ..."; updateBoot();
    entrance = await pickEntrance();
    updateBar();
    bootText = "Unpacking the client ..."; updateBoot();
    const names = FILE.files;
    const files = {};
    const transfer = [];
    for (const n of names) { files[n] = await fileBytes(n); transfer.push(files[n]); }
    // from a data: URL: browsers refuse module workers from blobs made by a file on disk
    const src = new TextDecoder().decode(files["worker.js"])
      .replace(/^import \{ loadPyodide as servedPyodide \}.*$/m, "const servedPyodide = null;")
      .replace(/^const HERE = import\.meta\.url;.*$/m, "const HERE = self.location.href;");
    delete files["worker.js"];
    const ws = new URL("onntp", entrance.url);
    ws.protocol = ws.protocol === "https:" ? "wss:" : "ws:";
    startWorker(new Worker("data:text/javascript;charset=utf-8," + encodeURIComponent(src), { type: "module" }),
      { files, entrance: ws.href }, Object.values(files));
  }

  if (FILE) {
    document.addEventListener("DOMContentLoaded", () => startFromFile().catch((err) => {
      bootText = "The client could not start: " + err.message; updateBoot(true);
    }));
  } else {
    // an app on the home screen: the client kept on the device by a service worker
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
    chooseTunnel().catch(() => null).then((ws) => startWorker(new Worker("/worker.js", { type: "module" }), ws ? { entrance: ws } : {}));
    setTimeout(() => checkVersions().catch(() => {}), 3000);
    setInterval(() => checkVersions().catch(() => {}), 6 * 3600 * 1000);
  }

  // --- when this entrance is gone: another one from the signed list (UN-RFC 0043 8.3) --
  // The page and the client may come from the device (the service worker); only the
  // tunnel needs an entrance, and any listed one will do.
  const LIST_KEY = "unseenet-entrances";            // the last signed list seen; checked again on use
  const ANCHOR = (() => {           // a lab's own key instead of the project's (build_web.py --list-owner)
    try { return JSON.parse(document.getElementById("unseenet-anchor").textContent) || {}; } catch { return {}; }
  })();
  const verifyList = (b) => (ANCHOR.owner ? __m.entrances.verifyBundle(b, ANCHOR.owner, ANCHOR.group)
                                          : __m.entrances.verifyBundle(b));
  async function answers(base, ms = 6000) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), ms);
    try { return (await realFetchEarly(new URL(".well-known/unseenet-entrance", base), { signal: ctl.signal, cache: "no-store" })).ok; }
    catch { return false; } finally { clearTimeout(t); }
  }
  async function refreshList() {
    try {
      const b = await (await realFetchEarly("/.well-known/unseenet-entrances.json", { cache: "no-store" })).json();
      verifyList(b);
      localStorage.setItem(LIST_KEY, JSON.stringify(b));
    } catch { /* keep the one we have */ }
  }
  function knownList() {
    try { return verifyList(JSON.parse(localStorage.getItem(LIST_KEY))); } catch { return null; }
  }
  async function chooseTunnel() {
    if (await answers(location.origin + "/")) { refreshList(); return null; }     // our own: as always
    bootText = "This entrance does not answer - looking for another one ..."; updateBoot();
    const list = knownList();
    if (!list) return null;
    const others = list.entrances.filter((e) => e.url && new URL(e.url).origin !== location.origin);
    const ok = (await Promise.all(others.map(async (e) => ((await answers(e.url)) ? e : null)))).filter(Boolean);
    if (!ok.length) return null;              // the client opens anyway: the vault can still be saved
    entrance = ok[Math.floor(Math.random() * ok.length)];
    updateBar();
    const ws = new URL("onntp", entrance.url);
    ws.protocol = ws.protocol === "https:" ? "wss:" : "ws:";
    return ws.href;
  }

  // --- the client kept on this device, and newer versions (UN-RFC 0043 7.3) --
  // The page keeps a version only when every file matches the SHA-256 that a post
  // in unseenet.releases gives, signed by a key the project key lists - wherever
  // the files came from. It offers newer versions and never goes back. A build
  // that is not signed (an entrance built for testing) is kept as it is, and says so.
  const META = "unseenet-meta", FRESH = "unseenet-fetch";   // ?unseenet-fetch: past the service worker
  let keptVersion = null, offer = null, swWarning = false, ownWeb = "";
  const verifyReleases = (b) => (ANCHOR.owner && ANCHOR.releases
    ? __m.releases.verifyReleases(b, ANCHOR.owner, ANCHOR.releases) : __m.releases.verifyReleases(b));
  const newer = (a, b) => __m.releases.compareVersions(a, b) > 0;
  async function hex(buf) {
    const d = new Uint8Array(await crypto.subtle.digest("SHA-256", buf));
    return Array.from(d, (x) => x.toString(16).padStart(2, "0")).join("");
  }
  async function getJSON(url, ms = 8000) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), ms);
    try {
      const r = await realFetchEarly(url, { signal: ctl.signal, cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      return await r.json();
    } finally { clearTimeout(t); }
  }
  async function readActive() {
    const r = await (await caches.open(META)).match("/__active");
    return r ? r.json() : null;
  }
  async function setActive(a) {
    await (await caches.open(META)).put("/__active", new Response(JSON.stringify(a), { headers: { "Content-Type": "application/json" } }));
    const reg = await navigator.serviceWorker.getRegistration().catch(() => null);
    for (const w of [navigator.serviceWorker.controller, reg && reg.active]) if (w) w.postMessage({ type: "active-changed" });
  }
  // the entrances to ask: this one, and two others from the signed list
  function otherEntrances(n = 2) {
    const list = knownList();
    const others = list ? list.entrances.filter((e) => e.url && new URL(e.url).origin !== location.origin) : [];
    return others.sort(() => Math.random() - 0.5).slice(0, n).map((e) => e.url);
  }
  // every valid version any of them has, newest first (7.3.1)
  async function signedVersions(bases) {
    const seen = new Map();
    await Promise.all(bases.map(async (base) => {
      try {
        for (const r of verifyReleases(await getJSON(new URL(".well-known/unseenet-releases.json", base))))
          if (!seen.has(r.version)) seen.set(r.version, r);
      } catch { /* that one has none, or cannot be trusted */ }
    }));
    return [...seen.values()].sort((a, b) => __m.releases.compareVersions(b.version, a.version));
  }
  async function serving(version, bases) {           // the entrances that serve this version
    const ok = await Promise.all(bases.map(async (b) => {
      try { return (await getJSON(new URL(".well-known/unseenet-entrance", b))).web === version ? b : null; }
      catch { return null; }
    }));
    return ok.filter(Boolean);
  }
  function parseSums(text) {
    const files = {};
    for (const line of text.split("\n")) {
      const m = /^([0-9a-f]{64})  (\S+)$/.exec(line);
      if (m) files[m[2]] = m[1];
    }
    return files;
  }
  // download, check and keep one version; then the service worker serves it
  async function keepVersion(version, files, bases, signed, progress = () => {}) {
    if (!bases.length) throw new Error("no entrance that answers has this version");
    const name = "unseenet-web-" + version;
    const c = await caches.open(name);
    const prev = await readActive().catch(() => null);
    const old = prev && prev.cache !== name ? await caches.open(prev.cache) : null;
    const paths = Object.keys(files).filter((p) => !p.endsWith(".template.html"));
    let done = 0;
    for (const path of paths) {
      let buf = null;
      for (const src of [old, c]) {                  // unchanged files: from what is kept
        const hit = src && await src.match("/" + path);
        if (hit) { const b = await hit.arrayBuffer(); if (await hex(b) === files[path]) { buf = b; break; } }
      }
      for (const base of bases) {
        if (buf) break;
        try {
          const u = new URL(path, base);
          if (u.origin === location.origin) u.searchParams.set(FRESH, "1");
          const r = await realFetchEarly(u, { cache: "no-store", mode: "cors" });
          const b = r.ok ? await r.arrayBuffer() : null;
          if (b && await hex(b) === files[path]) buf = b;
        } catch { /* the next entrance */ }
      }
      if (!buf) throw new Error(`${path} could not be fetched, or did not match the signed checksum`);
      await c.put("/" + path, new Response(buf));
      progress(++done, paths.length);
    }
    await setActive({ version, cache: name, signed });
    for (const k of await caches.keys()) if (k.startsWith("unseenet-web-") && k !== name) await caches.delete(k);
    keptVersion = { version, signed };
  }
  async function checkVersions() {
    if (FILE || !("caches" in window) || !window.isSecureContext || !navigator.serviceWorker) return;
    const own = location.origin + "/";
    const active = await readActive().catch(() => null);
    if (active) keptVersion = { version: active.version, signed: active.signed };
    try { ownWeb = (await getJSON(own + ".well-known/unseenet-entrance")).web || ""; } catch { ownWeb = ""; }
    const others = otherEntrances();
    const versions = await signedVersions([own, ...others]);
    if (!active && ownWeb) {                         // the first visit: keep what runs now
      const r = versions.find((x) => x.version === ownWeb);
      if (r) await keepVersion(ownWeb, r.files, [own], true);
      else {
        const sums = await realFetchEarly(`/release.sums?${FRESH}=1`, { cache: "no-store" });
        if (sums.ok) await keepVersion(ownWeb, parseSums(await sums.text()), [own], false);
      }
    }
    offer = null;
    if (keptVersion) {
      const best = versions[0];
      if (best && newer(best.version, keptVersion.version)) offer = best;
      else if (!keptVersion.signed && ownWeb && newer(ownWeb, keptVersion.version) && !versions.some((v) => v.version === ownWeb))
        offer = { version: ownWeb, unsigned: true };      // a newer test build here
    }
    // the tripwire (7.3): the service worker the entrance serves must be a signed one
    if (keptVersion && keptVersion.signed && versions.length) {
      try {
        const sw = await realFetchEarly(`/sw.js?${FRESH}=1`, { cache: "no-store" });
        if (sw.ok) swWarning = !new Set(versions.map((v) => v.files["sw.js"])).has(await hex(await sw.arrayBuffer()));
      } catch { /* the entrance is gone: nothing to compare */ }
    }
    updateBar();
  }
  function updateDialog() {
    const d = el("dialog", { class: "uw-dialog" });
    const status = el("p", { class: "uw-status" }, "");
    const unlocked = typeof S !== "undefined" && S.state && S.state.unlocked;
    const go = el("button", { class: "uw-btn uw-dirty", on: { onclick: async () => {
      go.disabled = true;
      try {
        const own = location.origin + "/";
        const bases = await serving(offer.version, [own, ...otherEntrances(6)]);
        const files = offer.unsigned
          ? parseSums(await (await realFetchEarly(`/release.sums?${FRESH}=1`, { cache: "no-store" })).text())
          : offer.files;
        status.textContent = "Fetching and checking ...";
        await keepVersion(offer.version, files, bases, !offer.unsigned, (n, all) => { status.textContent = `Checked ${n} of ${all} files ...`; });
        status.textContent = "Done. Starting the new version ...";
        if (kept && keepTimer) { clearTimeout(keepTimer); await storeVault().catch(() => {}); }
        vaultDirty = false;
        location.reload();
      } catch (err) { status.textContent = "Not updated: " + err.message; go.disabled = false; }
    } } }, "Update now");
    const when = offer.date ? " of " + offer.date.toISOString().slice(0, 10) : "";
    d.append(el("h2", {}, "A new version of the client"),
      el("p", {}, offer.unsigned
        ? `This entrance has a newer test build, ${offer.version}. It is not signed: use it only if you trust this entrance.`
        : `Version ${offer.version}${when}, signed by ${offer.byOwner ? "the Unseenet project's key" : "a key the Unseenet project lists"}. ` +
          "Every file is checked against the signature before it is used, whichever entrance it comes from."),
      unlocked && !kept ? el("p", { class: "uw-warn" }, "Your vault is only in this tab: save it as a file first, or it is gone when the page starts again.") : "",
      status,
      el("div", { class: "uw-row" }, go,
        unlocked && !kept ? el("button", { class: "uw-btn", on: { onclick: saveVault } }, "Save vault file") : "",
        el("button", { class: "uw-btn", on: { onclick: () => d.close() } }, "Later")));
    document.body.append(d);
    d.showModal();
    d.addEventListener("close", () => d.remove());
  }

  // --- the API, answered by the worker ------------------------------------
  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    // the core's API (UN-RFC 0046) and unseenet-gui's own routes under /x/gui/ (section 6)
    const ours = url.pathname.startsWith("/api/") || url.pathname.startsWith("/x/");
    if (url.origin !== location.origin || !ours) return realFetch(input, init);
    const r = await call({ type: "api", method: (init.method || "GET").toUpperCase(),
      path: url.pathname + url.search, body: init.body ?? null });
    let body = r.body;
    if (url.pathname === "/x/gui/state" && r.status === 200) {      // where the vault is
      try {
        const st = JSON.parse(body);
        st.vault = kept ? "kept on this device, encrypted with your passphrase" :
          "kept in this browser tab only - save it with the bar at the top";
        body = JSON.stringify(st);
      } catch { /* leave it */ }
    }
    return new Response(body, { status: r.status, headers: { "Content-Type": (r.type || "application/json") + "; charset=utf-8" } });
  };

  // --- the event stream ----------------------------------------------------
  const RealWebSocket = window.WebSocket;
  class EventSocket extends EventTarget {
    constructor() {
      super();
      this.readyState = 0; this.protocol = "unseenet";
      this.onopen = this.onmessage = this.onclose = this.onerror = null;
      sockets.add(this);
      setTimeout(() => { this.readyState = 1; this._fire("open", new Event("open")); }, 0);
    }
    _fire(type, ev) { this.dispatchEvent(ev); const f = this["on" + type]; if (f) f.call(this, ev); }
    _message(data) { if (this.readyState === 1) this._fire("message", new MessageEvent("message", { data })); }
    send() { }
    close() { if (this.readyState > 1) return; this.readyState = 3; sockets.delete(this); this._fire("close", new CloseEvent("close")); }
  }
  window.WebSocket = function (url, protocols) {
    const u = new URL(url, location.href);
    const ours = [].concat(protocols || []).includes("unseenet");     // the client's event stream
    if (ours || (u.host === location.host && u.pathname === "/api/events")) return new EventSocket();
    return new RealWebSocket(url, protocols);
  };
  Object.assign(window.WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });

  // the client wants a token in the address; the worker adds the real one itself
  if (!/[#&]t=/.test(location.hash)) history.replaceState(null, "", location.pathname + "#t=web");

  // the page's own state loader (unseenet-gui: the core's state with /x/gui/state)
  async function refreshState() {
    if (typeof loadState === "function") await loadState();
    else S.state = await api("GET", "/api/state");
  }

  // --- the vault as a file -------------------------------------------------
  async function openVault(file) {
    const bytes = await file.arrayBuffer();
    const r = await call({ type: "vault-in", bytes }, [bytes]);
    if (!r.ok) { alert(r.error || "Could not open that file"); return; }
    vaultDirty = false;
    if (typeof S !== "undefined" && typeof api === "function" && typeof render === "function") {
      await refreshState(); S.error = null; render();
    }
    updateBar();
  }

  async function saveVault() {
    const r = await call({ type: "vault-out" });
    if (!r.bytes) { alert("There is no vault yet: create one first."); return; }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([r.bytes], { type: "application/octet-stream" }));
    a.download = "unseenet.vault";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    vaultDirty = false; updateBar();
  }

  window.addEventListener("beforeunload", (e) => { if (vaultDirty && !kept) { e.preventDefault(); e.returnValue = ""; } });

  // --- the vault kept on this device (UN-RFC 0043 6.2: only when turned on) --
  // The stored file is the same encrypted vault as on a stick: without the
  // passphrase it is useless, but anyone with this device can try passphrases.
  let kept = false, keepTimer = null;
  const idb = () => new Promise((resolve, reject) => {
    const r = indexedDB.open("unseenet", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("vault");
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
  async function idbDo(mode, fn) {
    const db = await idb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction("vault", mode);
      const req = fn(tx.objectStore("vault"));
      tx.oncomplete = () => { resolve(req && req.result); db.close(); };
      tx.onerror = () => { reject(tx.error); db.close(); };
    });
  }
  async function storeVault() {
    const r = await call({ type: "vault-out" });
    if (!r.bytes) return false;
    await idbDo("readwrite", (s) => s.put({ bytes: r.bytes, saved: Date.now() }, "vault"));
    vaultDirty = false; updateBar();
    return true;
  }
  function keepSoon() {
    clearTimeout(keepTimer);
    keepTimer = setTimeout(() => storeVault().catch(() => { vaultDirty = true; updateBar(); }), 800);
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden" && kept && keepTimer) { clearTimeout(keepTimer); storeVault().catch(() => {}); }
  });
  async function restoreVault() {
    let rec = null;
    try { rec = await idbDo("readonly", (s) => s.get("vault")); } catch { return; }
    if (!rec || !rec.bytes) return;
    kept = true;
    const r = await call({ type: "vault-in", bytes: rec.bytes });
    if (r.ok && typeof S !== "undefined" && typeof api === "function" && typeof render === "function") {
      await refreshState(); S.error = null; render();
    }
    updateBar();
  }
  function keepDialog() {
    const d = el("dialog", { class: "uw-dialog" });
    const close = () => d.close();
    if (!kept) {
      d.append(el("h2", {}, "Keep your vault on this device?"),
        el("p", {}, "Then Unseenet opens straight to your passphrase next time - also from the home screen - and every change is kept by itself."),
        el("p", {}, "The vault stays encrypted with your passphrase, as on a stick. But anyone who gets hold of this device can try passphrases against it, and clearing the browser's data deletes it. Keep a copy as a file too, and choose a long passphrase."),
        el("div", { class: "uw-row" },
          el("button", { class: "uw-btn uw-dirty", on: { onclick: async () => {
            try {
              if (!(await storeVault())) { alert("Create or open a vault first."); return; }
              kept = true; navigator.storage?.persist?.().catch(() => {});
              updateBar(); close();
            } catch (err) { alert("This browser would not keep it: " + err.message); }
          } } }, "Keep it on this device"),
          el("button", { class: "uw-btn", on: { onclick: close } }, "Not now")));
    } else {
      d.append(el("h2", {}, "Your vault is on this device"),
        el("p", {}, "Encrypted with your passphrase. Changes are kept by themselves."),
        el("div", { class: "uw-row" },
          el("button", { class: "uw-btn", on: { onclick: () => { saveVault(); close(); } } }, "Save a copy as a file"),
          el("button", { class: "uw-btn", on: { onclick: async () => {
            if (!confirm("Delete the vault from this device? Without a copy as a file, this identity is gone.")) return;
            await idbDo("readwrite", (s) => s.delete("vault")).catch(() => {});
            kept = false; vaultDirty = true; updateBar(); close();
          } } }, "Forget it on this device"),
          el("button", { class: "uw-btn", on: { onclick: close } }, "Close")));
    }
    document.body.append(d);
    d.showModal();
    d.addEventListener("close", () => d.remove());
  }

  // --- the bar: always there (UN-RFC 0043 section 6.1) ----------------------
  const onion = location.hostname.endsWith(".onion");
  let bar, boot;
  function el(tag, attrs = {}, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) if (k === "on") Object.assign(e, v); else e.setAttribute(k, v);
    e.append(...kids);
    return e;
  }
  function updateBar() {
    if (!bar) return;
    bar.querySelector(".uw-text").textContent = entrance
      ? `Open web, through ${entrance.name} - it can see your IP address.`
      : onion ? "Tor Browser - the entrance cannot see your address." : "Open web - the entrance can see your IP address.";
    const unlocked = typeof S !== "undefined" && S.state && S.state.unlocked;
    bar.querySelector(".uw-open").hidden = !!unlocked || kept;
    const keep = bar.querySelector(".uw-keep");
    keep.hidden = FILE ? true : !unlocked && !kept;
    keep.textContent = kept ? "On this device ✓" : "Keep on this device";
    const upd = bar.querySelector(".uw-update");
    upd.hidden = !offer;
    const warn = bar.querySelector(".uw-swwarn");
    warn.hidden = !swWarning;
    const save = bar.querySelector(".uw-save");
    save.hidden = kept;
    save.classList.toggle("uw-dirty", vaultDirty);
    const narrow = window.matchMedia("(max-width: 760px)").matches;
    save.textContent = narrow ? (vaultDirty ? "Save vault •" : "Save vault") :
      (vaultDirty ? "Save vault file (changed)" : "Save vault file");
  }
  function updateBoot(failed) {
    if (!boot) return;
    boot.hidden = booted && !failed;
    boot.querySelector(".uw-boot-text").textContent = bootText;
  }
  // Carry on through another entrance: only the tunnel changes. The page, the vault
  // and everything open stay as they are, at this address.
  async function useEntrance(e) {
    const ws = new URL("onntp", e.url);
    ws.protocol = ws.protocol === "https:" ? "wss:" : "ws:";
    await call({ type: "entrance", url: ws.href });
    entrance = new URL(e.url).origin === location.origin ? null : e;
    updateBar();
  }

  // Other entrances: the signed list, which of them answer, and the list as a file
  // for when this one is blocked (UN-RFC 0043 section 8).
  async function entrancesDialog() {
    const d = el("dialog", { class: "uw-dialog" });
    const rows = el("tbody");
    const status = el("p", { class: "uw-status" }, "Checking the signed list of entrances ...");
    d.append(el("h2", {}, "Other entrances"),
      el("p", {}, "Unseenet can be reached through several web entrances, run by different people. " +
        "All of them serve the same signed client, so any of them will do: if this one is slow, blocked or gone, use another. " +
        "Use this one switches at once: the page and your vault stay as they are, only the connection goes through the other entrance."),
      el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Entrance"), el("th", {}, "Address"), el("th", {}, ""))), rows),
      status,
      el("p", {}, "Your vault kept on this device stays at this entrance's address. To use it at another one, ",
        el("button", { class: "uw-link", on: { onclick: saveVault } }, "save it as a file"), " and open it there."),
      el("h3", {}, "For when entrances are blocked"),
      el("p", {}, "Keep the list of entrances as a small file. Opened from your own disk, it checks the list's signature, " +
        "shows which entrances answer and opens one - also when this address does not work. Send it to friends too."),
      el("div", { class: "uw-row" },
        el("a", { class: "uw-btn", href: "/unseenet-entrances.html", download: "unseenet-entrances.html" }, "Save the list as a file"),
        el("button", { class: "uw-btn", on: { onclick: () => d.close() } }, "Close")));
    document.body.append(d);
    d.showModal();
    d.addEventListener("close", () => d.remove());
    await refreshList();
    const list = knownList();
    if (!list) { status.textContent = "This entrance has no signed list of entrances yet. Try again in a few minutes."; return; }
    status.textContent = `Signed list of ${list.updated || list.date.toISOString().slice(0, 10)}${ANCHOR.owner ? " (a TEST key)" : ""}. Asking each entrance ...`;
    let waiting = 0;
    for (const e of list.entrances) {
      const here = e.url && (entrance ? e.url === entrance.url : new URL(e.url).origin === location.origin);
      const state = el("td", {}, e.url ? (here ? "in use" : "...") : "Tor Browser only");
      const addr = e.url ? new URL(e.url).host : e.onion.slice(0, 16) + "....onion";
      rows.append(el("tr", { title: e.description || "" }, el("td", {}, e.name), el("td", {}, addr), state));
      if (e.url && !here) {
        waiting++;
        answers(e.url).then((ok) => {
          state.textContent = "";
          state.append(ok ? el("button", { class: "uw-btn uw-dirty", on: { onclick: async () => {
              await useEntrance(e);
              d.close();
            } } }, "Use this one")
                          : el("span", { class: "uw-warn" }, "does not answer"));
          if (--waiting === 0) status.textContent = status.textContent.replace(" Asking each entrance ...", "");
        });
      }
    }
    if (!waiting) {
      status.textContent = status.textContent.replace(" Asking each entrance ...", "") +
        " Right now this is the only entrance on the open web; the others are reached with Tor Browser. " +
        "There will be more as people run their own (see unseenet.org/web).";
    }
  }

  function aboutDialog() {
    const rows = [
      ["Groups and nodes can be removed by someone", "No", "No"],
      ["Others can delete or change your signed posts", "No", "No"],
      ["Nodes learn your IP address", "No", "No"],
      ["The entrance learns your IP address", "(no entrance)", onion ? "No (Tor Browser)" : "Yes"],
      ["You must trust code served to you", "Once, signed", "Every visit"],
      ["Easy to block", "Hard", onion ? "Hard" : "This entrance, yes"],
    ];
    const d = el("dialog", { class: "uw-dialog" },
      el("h2", {}, onion ? "You are in Tor Browser" : "You are on the open web"),
      el("p", {}, "This is Unseenet's graphical client, running in your browser. Your keys stay here: this page signs, checks signatures and pays the postage itself. The entrance only carries bytes to a node over Tor."),
      el("table", {}, el("tr", {}, el("th"), el("th", {}, "Real client"), el("th", {}, "This page")),
        ...rows.map(([a, b, c]) => el("tr", {}, el("td", {}, a), el("td", {}, b), el("td", { class: c.startsWith("Yes") || c.startsWith("Every") || c.startsWith("This") ? "uw-warn" : "" }, c)))),
      el("p", {}, kept ? "Your vault is kept on this device, encrypted with your passphrase. Keep a copy as a file too." :
        "Your vault lives only in this tab. Save it as a file to use it again, or keep it on this device. If you need to hide that you use Unseenet, or who you are, use the real client from unseenet.org."),
      el("p", {}, keptVersion ? `Version ${keptVersion.version}, ${keptVersion.signed ? "signed - every file checked" : "a test build, not signed"}, kept on this device.`
        : (ownWeb ? `Version ${ownWeb}, from this entrance.` : "")),
      el("p", {}, "If this entrance is ever blocked: ",
        el("a", { href: (FILE && entrance ? entrance.url : "/") + "unseenet-entrances.html", download: "unseenet-entrances.html" }, "save the list of entrances"),
        " - a small file that finds another one, from your own disk. Send it to friends too."),
      el("form", { method: "dialog" }, el("button", { class: "uw-btn" }, "Close")));
    document.body.append(d);
    d.showModal();
    d.addEventListener("close", () => d.remove());
  }
  document.addEventListener("DOMContentLoaded", () => {
    document.documentElement.classList.add("uw");
    const file = el("input", { type: "file", hidden: "" });
    file.onchange = () => { if (file.files[0]) openVault(file.files[0]); file.value = ""; };
    bar = el("div", { class: "uw-bar" + (onion ? " uw-tor" : ""), role: "status" },
      el("span", { class: "uw-text" }, onion ? "Tor Browser - the entrance cannot see your address." : "Open web - the entrance can see your IP address."),
      el("button", { class: "uw-link", on: { onclick: aboutDialog } }, window.matchMedia("(max-width: 760px)").matches ? "Why?" : "What does that mean?"),
      FILE ? el("button", { class: "uw-link", on: { onclick: () => { if (!vaultDirty || confirm("Your vault has changes you have not saved. Leave anyway?")) location.reload(); } } }, "Another entrance")
        : el("button", { class: "uw-link", on: { onclick: entrancesDialog } }, "Other entrances"),
      el("button", { class: "uw-link uw-warn uw-swwarn", hidden: "", on: { onclick: () => alert(
        "This entrance now serves a service worker - the part of the app that the browser fetches by itself - " +
        "that is not in any signed version. It could change what runs here. Unless the entrance's operator says why, " +
        "use another entrance, or the client in a file.") } }, "Warning: unsigned worker"),
      el("span", { class: "uw-grow" }),
      el("button", { class: "uw-btn uw-dirty uw-update", hidden: "", on: { onclick: updateDialog } }, "New version"),
      el("button", { class: "uw-btn uw-open", on: { onclick: () => file.click() } }, "Open vault file"),
      el("button", { class: "uw-btn uw-keep", on: { onclick: keepDialog } }, "Keep on this device"),
      el("button", { class: "uw-btn uw-save", on: { onclick: saveVault } }, "Save vault file"),
      file);
    boot = el("div", { class: "uw-boot" }, el("div", { class: "uw-boot-box" },
      el("div", { class: "uw-boot-text" }, bootText),
      el("div", { class: "uw-boot-note" }, "The first visit loads about 15 MB: Python and the Unseenet core run here, in your browser.")));
    document.body.prepend(bar);
    document.body.append(boot);
    updateBar(); updateBoot();
    setInterval(updateBar, 1500);
  });
  restoreVault();          // a vault kept on this device opens straight to its passphrase
})();
