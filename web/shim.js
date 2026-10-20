// Unseenet on the web: the graphical client (unseenet-gui) unchanged, with its
// backend in a Web Worker instead of on 127.0.0.1 (UN-RFC 0043 section 6).
// This script runs before the client's app.js and
//  - answers its fetch("/api/...") and WebSocket("/api/events") from the worker,
//  - shows, always, that this is the open web (section 6.1),
//  - opens and saves the vault as a file (section 6.2).
(() => {
  "use strict";
  const worker = new Worker("/worker.js", { type: "module" });
  const pending = new Map();
  const sockets = new Set();
  let nextId = 1, booted = false, vaultDirty = false, bootText = "Starting ...";

  const call = (msg, transfer = []) => new Promise((resolve) => {
    const id = nextId++;
    pending.set(id, resolve);
    worker.postMessage({ ...msg, id }, transfer);
  });

  worker.onmessage = (e) => {
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
  };

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
    if (u.host === location.host && u.pathname === "/api/events") return new EventSocket();
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
    const unlocked = typeof S !== "undefined" && S.state && S.state.unlocked;
    bar.querySelector(".uw-open").hidden = !!unlocked;
    const save = bar.querySelector(".uw-save");
    save.classList.toggle("uw-dirty", vaultDirty);
    save.textContent = vaultDirty ? "Save vault file (changed)" : "Save vault file";
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
      el("button", { class: "uw-link", on: { onclick: aboutDialog } }, "What does that mean?"),
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
