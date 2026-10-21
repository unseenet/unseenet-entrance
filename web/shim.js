// Unseenet on the web: the graphical client (unseenet-gui) unchanged, with its
// backend in a Web Worker instead of on 127.0.0.1 (UN-RFC 0043 section 6).
// This script runs before the client's app.js and
//  - answers its fetch("/api/...") and WebSocket("/api/events") from the worker,
//  - shows, always, that this is the open web (section 6.1),
//  - opens and saves the vault as a file (section 6.2).
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
      vaultDirty = true; updateBar();
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
    startWorker(new Worker("/worker.js", { type: "module" }), {});
  }

  // --- the API, answered by the worker ------------------------------------
  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith("/api/")) return realFetch(input, init);
    const r = await call({ type: "api", method: (init.method || "GET").toUpperCase(),
      path: url.pathname + url.search, body: init.body ?? null });
    let body = r.body;
    if (url.pathname === "/api/state" && r.status === 200) {
      try {
        const st = JSON.parse(body);
        st.vault = "kept in this browser tab only - save it with the bar at the top";
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

  // --- the vault as a file -------------------------------------------------
  async function openVault(file) {
    const bytes = await file.arrayBuffer();
    const r = await call({ type: "vault-in", bytes }, [bytes]);
    if (!r.ok) { alert(r.error || "Could not open that file"); return; }
    vaultDirty = false;
    if (typeof S !== "undefined" && typeof api === "function" && typeof render === "function") {
      S.state = await api("GET", "/api/state"); S.error = null; render();
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

  window.addEventListener("beforeunload", (e) => { if (vaultDirty) { e.preventDefault(); e.returnValue = ""; } });

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
    if (FILE && entrance) {
      bar.querySelector(".uw-text").textContent = onion ? "" :
        `Open web, through ${entrance.name} - it can see your IP address.`;
    }
    const unlocked = typeof S !== "undefined" && S.state && S.state.unlocked;
    bar.querySelector(".uw-open").hidden = !!unlocked;
    const save = bar.querySelector(".uw-save");
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
      el("p", {}, "Your vault lives only in this tab. Save it as a file to use it again, and keep the file like a password. If you need to hide that you use Unseenet, or who you are, use the real client from unseenet.org."),
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
        : el("a", { class: "uw-link", href: "/unseenet-entrances.html", download: "unseenet-entrances.html" }, "Other entrances"),
      el("span", { class: "uw-grow" }),
      el("button", { class: "uw-btn uw-open", on: { onclick: () => file.click() } }, "Open vault file"),
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
})();
