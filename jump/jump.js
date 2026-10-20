// The jump page (UN-RFC 0043 section 8.7): a file to keep, so that when one
// entrance is blocked you can find another. The signed list is built into the
// file; this script checks it against the project key, asks which entrances
// answer, and opens one. It can also fetch a newer list and save itself again.
import { PROJECT_KEY, PROJECT_GROUP, verifyBundle } from "./entrances.js";
import { fingerprint, keyFromText } from "./article.js";

const OWNER = document.documentElement.dataset.owner || PROJECT_KEY;
const GROUP = document.documentElement.dataset.group || PROJECT_GROUP;
const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) if (k === "class") e.className = v; else e.setAttribute(k, v);
  e.append(...kids.filter((k) => k != null));
  return e;
};

let bundle = JSON.parse($("unseenet-bundle").textContent || "null");
let list = null;
const status = new Map();          // url -> "yes" | "no" | "checking"

function check() {
  try {
    list = verifyBundle(bundle, OWNER, GROUP);
    $("trust").className = "trust ok";
    const who = OWNER === PROJECT_KEY ? "the Unseenet project" : "a TEST key, not the Unseenet project";
    $("trust").textContent = `✓ Signed by ${who} · list of ${list.updated || list.date.toISOString().slice(0, 10)} · ${list.entrances.length} entrance${list.entrances.length === 1 ? "" : "s"}`;
  } catch (err) {
    list = null;
    $("trust").className = "trust bad";
    $("trust").textContent = "✗ This list cannot be trusted: " + err.message;
  }
  render();
}

function render() {
  const tb = $("rows");
  tb.replaceChildren();
  if (!list) return;
  for (const e of list.entrances) {
    const st = e.url ? status.get(e.url) || "checking" : "onion";
    const where = el("td", {},
      e.url ? el("a", { href: e.url, rel: "noreferrer" }, e.url.replace(/^https?:\/\//, "").replace(/\/$/, "")) : null,
      e.url && e.onion ? el("br") : null,
      e.onion ? el("a", { href: `http://${e.onion}/`, rel: "noreferrer", class: "onion" }, e.onion.slice(0, 16) + "….onion") : null);
    const label = { yes: "answers", no: "no answer", checking: "checking …", onion: "Tor Browser" }[st];
    tb.append(el("tr", {},
      el("td", {}, el("b", {}, e.name), e.description ? el("div", { class: "muted" }, e.description) : null),
      where, el("td", {}, el("span", { class: "st " + st }, label))));
  }
  const ok = list.entrances.filter((e) => status.get(e.url) === "yes");
  $("random").disabled = !ok.length;
  $("random").textContent = ok.length ? `Open a random one that answers (${ok.length})` : "Waiting for an entrance to answer …";
}

async function probe(url) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 8000);
  try {
    const r = await fetch(new URL(".well-known/unseenet-entrance", url), { signal: ctl.signal, cache: "no-store" });
    return r.ok;
  } catch { return false; } finally { clearTimeout(t); }
}

async function probeAll() {
  if (!list) return;
  await Promise.all(list.entrances.filter((e) => e.url).map(async (e) => {
    status.set(e.url, "checking"); render();
    status.set(e.url, (await probe(e.url)) ? "yes" : "no"); render();
  }));
}

/** Ask the entrances that answer for their copy; keep it if it checks out and is newer. */
async function update() {
  const btn = $("update");
  btn.disabled = true; btn.textContent = "Looking …";
  let newer = null;
  for (const e of (list ? list.entrances : []).filter((x) => status.get(x.url) === "yes")) {
    try {
      const r = await fetch(new URL(".well-known/unseenet-entrances.json", e.url), { cache: "no-store" });
      const b = await r.json();
      const l = verifyBundle(b, OWNER, GROUP);
      if (!list || l.date > list.date) { newer = b; list = l; }
    } catch { /* that one had nothing better */ }
  }
  if (newer) {
    bundle = newer; check(); probeAll();
    $("save").hidden = false;
    btn.textContent = "Found a newer list - save this file again to keep it";
  } else {
    btn.textContent = "This is the newest list";
  }
  btn.disabled = false;
}

/** This page again, with the newest list in it. */
function save() {
  const doc = document.documentElement.cloneNode(true);
  doc.querySelector("#unseenet-bundle").textContent = JSON.stringify(bundle);
  doc.querySelector("#rows").replaceChildren();
  const html = "<!doctype html>\n" + doc.outerHTML;
  const a = el("a", { download: "unseenet-entrances.html",
    href: URL.createObjectURL(new Blob([html], { type: "text/html" })) });
  document.body.append(a); a.click(); a.remove();
}

$("fp").textContent = fingerprint(keyFromText(OWNER));
$("random").onclick = () => {
  const ok = list.entrances.filter((e) => status.get(e.url) === "yes");
  if (ok.length) location.href = ok[Math.floor(Math.random() * ok.length)].url;
};
$("update").onclick = update;
$("save").onclick = save;
check();
probeAll();
