// Unseenet on the web - a first web client (UN-RFC 0043).
// Everything with keys happens here, in the browser. The entrance only carries bytes.
import { Connection } from "./nntp.js";
import { Article, Identity, MARKDOWN_TYPE, fingerprint, keyFromText, manifestData, newArticle, splitGroupId } from "./article.js";
import { b64, b64decode } from "./encoding.js";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

const S = {
  conn: null, onion: null, minPow: 18, caps: [],
  groups: [], group: null,                 // group: {gid, manifest, overview, chat}
  me: null, myName: "", mine: new Set(),
};

// --- where we are (UN-RFC 0043 6.1) ------------------------------------------

function showWhere() {
  const onion = location.hostname.endsWith(".onion");
  if (onion) {
    $("where").classList.add("tor");
    $("where-text").textContent = "Tor Browser - the entrance cannot see your address, but you trust the code it serves.";
  }
  $("where-more").onclick = () => $("dlg-about").showModal();
  fetch(".well-known/unseenet-entrance").then((r) => r.json()).then((info) => {
    $("about-entrance").textContent = `This entrance: ${info.software || ""} · operator ${info.operator || "unknown"} · ` +
      `${info.statement || ""}${info.onion ? " · onion " + info.onion : ""}`;
  }).catch(() => {});
}

// --- connecting ------------------------------------------------------------

function status(text, kind = "") {
  const s = $("status");
  s.textContent = text;
  s.className = "status " + kind;
}

async function loadNodes() {
  const sel = $("node");
  let nodes = [];
  try { nodes = (await (await fetch("nodes.json")).json()).nodes || []; } catch { /* none */ }
  for (const n of nodes) {
    const o = el("option", null, n.name);
    o.value = n.address.split(":")[0];
    o.title = n.description || "";
    sel.append(o);
  }
  const stable = nodes.find((n) => /stable/i.test(n.description || ""));
  if (stable) sel.value = stable.address.split(":")[0];
  sel.onchange = () => connect();
}

async function connect() {
  S.conn?.close();
  S.conn = null;
  S.onion = $("node").value;
  if (!S.onion) { status("no node", "bad"); return; }
  status("connecting through Tor ...", "busy");
  try {
    S.conn = await Connection.open(location.href, S.onion);
    S.caps = await S.conn.capabilities();
    const mp = S.caps.find((c) => c.startsWith("UNSEENET-MINPOW "));
    S.minPow = mp ? parseInt(mp.split(" ")[1], 10) : 18;
    status("connected", "ok");
    await loadGroups();
    if (S.group) await openGroup(S.group.gid);
  } catch (err) {
    status("not connected", "bad");
    showError(`Could not reach the node: ${err.message}`);
  }
}

/** Run ``fn(conn)``; if the tunnel died, connect again once and retry. */
async function withConn(fn) {
  if (!S.conn || S.conn.closed) await connect();
  try { return await fn(S.conn); } catch (err) {
    if (!S.conn || !S.conn.closed) throw err;
    await connect();
    return fn(S.conn);
  }
}

function showError(text) {
  const r = $("reader");
  r.replaceChildren(el("div", "note", text));
}

// --- groups ------------------------------------------------------------------

async function loadGroups() {
  const [active, names] = await withConn(async (c) => [
    await c.multiline("LIST ACTIVE", 215), await c.multiline("LIST NEWSGROUPS *", 215)]);
  const desc = new Map(names.map((l) => { const i = l.indexOf("\t"); return [l.slice(0, i), l.slice(i + 1)]; }));
  S.groups = active.map((l) => {
    const [gid, high] = l.split(/\s+/);
    const [name] = splitGroupId(gid);
    return { gid, name, high: +high, desc: desc.get(gid) || "" };
  }).sort((a, b) => a.name.localeCompare(b.name));
  renderGroups();
}

function renderGroups() {
  const f = $("filter").value.trim().toLowerCase();
  const ul = $("group-list");
  ul.replaceChildren();
  for (const g of S.groups) {
    if (f && !g.name.includes(f) && !g.desc.toLowerCase().includes(f)) continue;
    const li = el("li");
    li.append(el("div", "gname", g.name), el("div", "gdesc", g.desc));
    if (S.group?.gid === g.gid) li.classList.add("sel");
    li.onclick = () => openGroup(g.gid);
    ul.append(li);
  }
}

async function openGroup(gid) {
  document.body.classList.add("in-group");
  document.body.classList.remove("reading");
  const g = S.groups.find((x) => x.gid === gid) || { gid, name: splitGroupId(gid)[0], desc: "" };
  $("group-title").textContent = g.name;
  $("group-desc").textContent = g.desc;
  $("thread-list").replaceChildren(el("li", "muted", "Loading ..."));
  $("reader").replaceChildren(el("div", "empty muted", "Pick a post to read it."));
  const { range, manifest, overview } = await withConn(async (c) => {
    const range = await c.group(gid);
    let manifest = null;
    if (S.caps.some((x) => x.startsWith("UNSEENET-MANIFESTS"))) {
      const raw = await c.manifest(gid);
      if (raw) manifest = manifestData(Article.fromBytes(raw), gid);
    }
    let overview = [];
    if (range.count > 0) {
      const lines = await c.multiline(`OVER ${Math.max(range.low, range.high - 199)}-${range.high}`, 224);
      // group settings and control messages are not posts to read
      overview = lines.map(parseOver).filter((o) => o && o.from && o.from !== "(unknown)");
    }
    return { range, manifest, overview };
  });
  const kinds = manifest?.kinds || ["chat", "post"];
  const chatView = manifest?.view === "chat" || (kinds.length === 1 && kinds[0] === "chat");
  S.group = { gid, name: g.name, manifest, overview, kinds, chat: chatView, range };
  renderGroups();
  renderGroup();
}

function parseOver(line) {
  const f = line.split("\t");
  if (f.length < 8) return null;
  return { n: +f[0], subject: f[1], from: f[2], date: f[3], msgid: f[4],
    refs: f[5] ? f[5].split(/\s+/).filter(Boolean) : [] };
}

/** May we write here, and if not, why (checked before anyone writes). */
function writePermission() {
  const m = S.group?.manifest;
  if (!S.me) return [false, "Make an identity (top right) to write."];
  if (!m) return [true, ""];
  if (m.type === "open") return [true, ""];
  const me = S.me.keyText;
  if (m.owner === me || (m.moderators || []).includes(me)) return [true, ""];
  if (m.type === "closed") return [false, "A closed group: its posts are encrypted for members. Not in the web client yet."];
  if (m.type === "moderated") return [false, "A moderated group: posts go to the moderators first. Not in the web client yet."];
  if (m.type === "channel") return [false, "A channel: only its owner and posters write here."];
  return [false, "This web client cannot write here yet."];
}

function renderGroup() {
  const G = S.group;
  const [may, why] = writePermission();
  const takesPosts = G.kinds.includes("post"), takesChat = G.kinds.includes("chat");
  $("new-btn").hidden = !takesPosts;
  $("new-btn").disabled = !may;
  $("view-btn").hidden = !(takesPosts && takesChat);
  $("view-btn").textContent = G.chat ? "Threads view" : "Chat view";
  $("write-note").hidden = may;
  if ($("reply-btn")) $("reply-btn").disabled = !may;
  $("write-note").textContent = why;
  $("thread-list").hidden = G.chat;
  $("chat").hidden = !G.chat;
  $("chat-input").disabled = !may || !takesChat;
  $("chat-input").placeholder = !takesChat ? "This group takes posts only" : may ? "Write a chat line" : why;
  if (G.chat) renderChat(); else renderThreads();
}

function renderThreads() {
  const G = S.group;
  const ul = $("thread-list");
  ul.replaceChildren();
  if (!G.overview.length) { ul.append(el("li", "muted", "No posts yet.")); return; }
  // a tree from References: children under their nearest known parent
  const byId = new Map(G.overview.map((o) => [o.msgid, { ...o, kids: [] }]));
  const roots = [];
  for (const node of byId.values()) {
    const parent = [...node.refs].reverse().find((r) => byId.has(r));
    if (parent) byId.get(parent).kids.push(node); else roots.push(node);
  }
  const latest = (t) => Math.max(t.n, ...t.kids.map(latest));
  roots.sort((a, b) => latest(b) - latest(a));
  const add = (node, depth) => {
    const li = el("li");
    const subj = el("span", "subj", (depth ? "↳ ".padStart(depth * 2 + 2, "\u00a0") : "") + (node.subject || "(chat line)"));
    li.append(subj, el("span", "when", shortDate(node.date)), el("span", "who", node.from));
    if (S.mine.has(node.msgid)) li.classList.add("mine");
    li.onclick = () => { ul.querySelectorAll(".sel").forEach((x) => x.classList.remove("sel")); li.classList.add("sel"); readPost(node.msgid); };
    ul.append(li);
    node.kids.sort((a, b) => a.n - b.n).forEach((k) => add(k, depth + 1));
  };
  roots.forEach((r) => add(r, 0));
}

async function renderChat() {
  const box = $("chat-lines");
  box.replaceChildren(el("div", "muted", "Loading ..."));
  const recent = S.group.overview.slice(-40);
  const arts = [];
  await withConn(async (c) => {
    for (const o of recent) {
      const raw = await c.article(o.msgid);
      if (raw) arts.push(Article.fromBytes(raw));
    }
  });
  box.replaceChildren();
  for (const a of arts) {
    const mine = a.get("Author-Key") === S.me?.keyText;
    const d = el("div", "chat-line" + (mine ? " mine" : ""));
    d.append(el("div", "who", `${author(a)} · ${shortDate(a.get("Date"))}${a.verify() ? "" : " · ✗ signature does not verify"}`),
      el("div", "body", a.body.trimEnd()));
    box.append(d);
  }
  box.scrollTop = box.scrollHeight;
}

// --- reading -----------------------------------------------------------------

function author(art) {
  let fp = "";
  try { fp = "~" + fingerprint(keyFromText(art.get("Author-Key"))).slice(0, 4); } catch { /* none */ }
  return (art.get("From") || "anonymous") + fp;
}

function shortDate(value) {
  const d = new Date(value);
  if (isNaN(d)) return value || "";
  const today = new Date().toDateString() === d.toDateString();
  return today ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString([], { day: "numeric", month: "short" });
}

/** Markdown, safely and simply: quotes, `code`, **bold**, *emphasis*. Links stay text. */
function renderBody(text, markdown) {
  const out = el("div", "body");
  const lines = text.replace(/\n$/, "").split("\n");
  let quote = null;
  for (const line of lines) {
    if (line.startsWith(">")) {
      if (!quote) { quote = el("blockquote"); out.append(quote); }
      quote.append(inline(line.replace(/^>\s?/, ""), markdown), "\n");
      continue;
    }
    quote = null;
    out.append(inline(line, markdown), "\n");
  }
  return out;
}

function inline(line, markdown) {
  const span = el("span");
  if (!markdown) { span.textContent = line; return span; }
  const re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/g;
  let last = 0, m;
  while ((m = re.exec(line))) {
    span.append(line.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("`")) span.append(el("code", null, t.slice(1, -1)));
    else if (t.startsWith("**")) span.append(el("b", null, t.slice(2, -2)));
    else span.append(el("i", null, t.slice(1, -1)));
    last = m.index + t.length;
  }
  span.append(line.slice(last));
  return span;
}

async function readPost(msgid) {
  document.body.classList.add("reading");
  const r = $("reader");
  r.replaceChildren(el("div", "muted", "Fetching through Tor ..."));
  const raw = await withConn((c) => c.article(msgid));
  if (!raw) { r.replaceChildren(el("div", "note", "The node does not have this post (any more).")); return; }
  const art = Article.fromBytes(raw);
  const ok = art.verify();
  const back = el("button", "btn small back", "← Back");
  back.onclick = () => document.body.classList.remove("reading");
  const meta = el("div", "meta");
  meta.append(el("span", null, author(art)), el("span", null, art.get("Date") || ""),
    el("span", "sig " + (ok ? "ok" : "bad"), ok ? "✓ signed" : "✗ signature does not verify"));
  if (art.get("Supersedes")) meta.append(el("span", null, "(edited)"));
  const markdown = (art.get("Content-Type") || "").startsWith("text/markdown");
  r.replaceChildren(back, el("h1", null, art.get("Subject") || "(no subject)"), meta, renderBody(art.body, markdown));
  const [may] = writePermission();
  if (S.group.kinds.includes("post")) {
    const row = el("div", "row");
    const reply = el("button", "btn primary", "Reply");
    reply.id = "reply-btn";
    reply.disabled = !may;
    reply.onclick = () => compose(art);
    row.append(reply);
    r.append(row);
  }
}

// --- writing -----------------------------------------------------------------

function compose(parent = null) {
  const d = $("dlg-post");
  $("post-title").textContent = parent ? "Reply" : `New post in ${S.group.name}`;
  $("post-subject").value = parent ? "Re: " + (parent.get("Subject") || "").replace(/^(Re:\s*)+/i, "") : "";
  $("post-body").value = parent
    ? `${author(parent)} wrote:\n` + parent.body.trimEnd().split("\n").map((l) => (l.startsWith(">") ? ">" + l : "> " + l)).join("\n") + "\n\n"
    : "";
  $("post-status").textContent = "";
  $("post-send").disabled = false;
  d.parentRef = parent;
  d.showModal();
  ($("post-subject").value ? $("post-body") : $("post-subject")).focus();
}

async function send({ subject, body, kind, parent }) {
  const G = S.group;
  const refs = parent ? [...((parent.get("References") || "").split(/\s+/).filter(Boolean)), parent.messageId].slice(-20) : null;
  let bits = Math.max(S.minPow, G.manifest?.min_pow || 0);
  for (let attempt = 0; attempt < 2; attempt++) {
    const art = newArticle(G.gid, kind === "chat" ? "" : subject, body, {
      fromName: S.myName, references: refs,
      extra: [["Content-Type", MARKDOWN_TYPE], ["Message-Kind", kind]],
    });
    await art.finalize(S.me, bits, (p) => { $("post-status").textContent = `Paying postage (${bits} bits) ... ${Math.round(p * 100)}%`; });
    try {
      await withConn((c) => c.post(art.toBytes()));
      S.mine.add(art.messageId);
      return art;
    } catch (err) {
      const m = /need (\d+) bits/.exec(err.message || "");
      if (!m || +m[1] <= bits || +m[1] > 28) throw err;
      bits = +m[1];
    }
  }
  throw new Error("the node keeps asking for more postage");
}

// --- identity (in memory; a key file until vaults come) ---------------------

function renderMe() {
  $("me-name").textContent = S.me ? `${S.myName}~${S.me.short}` : "No identity";
  $("me-none").hidden = !!S.me;
  $("me-some").hidden = !S.me;
  if (S.me) {
    $("me-show-name").textContent = S.myName;
    $("me-show-fp").textContent = "~" + S.me.short;
    $("me-show-key").textContent = S.me.keyText;
  }
  if (S.group) renderGroup();
}

function setupIdentity() {
  $("me-btn").onclick = () => $("dlg-me").showModal();
  $("me-create").onclick = () => {
    const name = $("me-new-name").value.trim();
    if (!name) { $("me-new-name").focus(); return; }
    S.me = Identity.generate();
    S.myName = name;
    renderMe();
  };
  $("me-save").onclick = () => {
    const data = { format: "unseenet-web-key", v: 0, note: "Not a vault: anyone with this file can post as you.",
      name: S.myName, key: S.me.keyText, seed: b64(S.me.seed) };
    const a = el("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    a.download = `unseenet-${S.myName}-${S.me.short}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  };
  $("me-file").onchange = async (e) => {
    try {
      const data = JSON.parse(await e.target.files[0].text());
      if (data.format !== "unseenet-web-key") throw new Error("not an Unseenet web key file");
      const id = new Identity(b64decode(data.seed));
      if (id.keyText !== data.key) throw new Error("the file is damaged");
      S.me = id; S.myName = data.name || "anonymous";
      renderMe();
    } catch (err) { alert(err.message); }
    e.target.value = "";
  };
  $("me-forget").onclick = () => { S.me = null; S.myName = ""; renderMe(); };
}

// --- start -------------------------------------------------------------------

function setupWriting() {
  $("new-btn").onclick = () => compose(null);
  $("post-cancel").onclick = () => $("dlg-post").close();
  $("view-btn").onclick = () => { S.group.chat = !S.group.chat; renderGroup(); };
  $("post-form").onsubmit = async (e) => {
    e.preventDefault();
    const d = $("dlg-post");
    $("post-send").disabled = true;
    try {
      await send({ subject: $("post-subject").value.trim(), body: $("post-body").value, kind: "post", parent: d.parentRef });
      d.close();
      await openGroup(S.group.gid);
    } catch (err) {
      $("post-status").textContent = "Not sent: " + err.message;
      $("post-send").disabled = false;
    }
  };
  $("chat-form").onsubmit = async (e) => {
    e.preventDefault();
    const input = $("chat-input");
    const text = input.value.trim();
    if (!text) return;
    input.disabled = true;
    try {
      await send({ subject: "", body: text, kind: "chat", parent: null });
      input.value = "";
      await openGroup(S.group.gid);
    } catch (err) { alert("Not sent: " + err.message); }
    input.disabled = false;
    input.focus();
  };
}

showWhere();
setupIdentity();
setupWriting();
$("filter").oninput = renderGroups;
$("groups-back").onclick = () => document.body.classList.remove("in-group", "reading");
loadNodes().then(connect);
