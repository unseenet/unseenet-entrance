// The Unseenet article (UN-RFC 0002), identities (0003) and postage (0006).
import { ed } from "./vendor/noble.js";
import { DS, H, b32, b32decode, b64, b64decode, concat, utf8, fromUtf8 } from "./encoding.js";

export const PROTOCOL_VERSION = "1";
export const PURPOSE_ARTICLE = "UNSEENET-ARTICLE-1";
export const MARKDOWN_TYPE = "text/markdown; charset=utf-8; variant=unseenet";
const EXCLUDED = new Set(["message-id", "signature", "proof-of-work", "member-signature",
  "approved-by", "approval"]);
const READER_ONLY = new Set(["path", "xref"]);

// --- keys and identities ----------------------------------------------------

export const keyToText = (pk) => "ed25519:" + b32(pk);

export function keyFromText(text) {
  text = text.trim();
  if (!text.startsWith("ed25519:")) throw new Error("unknown key algorithm");
  const raw = b32decode(text.slice(8));
  if (raw.length !== 32) throw new Error("malformed key");
  return raw;
}

export const fingerprint = (pk) => b32(H(concat(DS("UNSEENET-FP-1"), pk))).slice(0, 16);

export class Identity {
  constructor(seed) {
    this.seed = seed;
    this.publicKey = ed.getPublicKey(seed);
  }
  static generate() { return new Identity(ed.utils.randomSecretKey()); }
  get keyText() { return keyToText(this.publicKey); }
  get fingerprint() { return fingerprint(this.publicKey); }
  /** Short form people see next to a name, like the TUI's name~abcd. */
  get short() { return this.fingerprint.slice(0, 4); }
  signText(purpose, message) {
    return "ed25519:" + b64(ed.sign(concat(DS(purpose), message), this.seed));
  }
}

export function verifySig(pk, purpose, message, sig) {
  try {
    return ed.verify(sig, concat(DS(purpose), message), pk, { zip215: false });
  } catch { return false; }
}

// --- groups -----------------------------------------------------------------

export function splitGroupId(gid) {
  const at = gid.lastIndexOf("@");
  if (at < 1) throw new Error("invalid group ID " + gid);
  return [gid.slice(0, at), gid.slice(at + 1)];
}

// --- dates ------------------------------------------------------------------

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const p2 = (n) => String(n).padStart(2, "0");

/** RFC 5322 date in UTC, rounded down to 10 minutes (as the reference client). */
export function formatDate(d = new Date()) {
  const m = d.getUTCMinutes() - (d.getUTCMinutes() % 10);
  return `${DAYS[d.getUTCDay()]}, ${p2(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]} ` +
    `${d.getUTCFullYear()} ${p2(d.getUTCHours())}:${p2(m)}:00 +0000`;
}

// --- articles ---------------------------------------------------------------

export function normalizeBody(body) {
  body = body.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  return body.endsWith("\n") ? body : body + "\n";
}

export class Article {
  constructor(headers = [], body = "") {
    this.headers = [];
    for (const [n, v] of headers) this.set(n, v);
    this.body = normalizeBody(body);
  }

  get(name, dflt = null) {
    const l = name.toLowerCase();
    for (const [n, v] of this.headers) if (n.toLowerCase() === l) return v;
    return dflt;
  }

  set(name, value) {
    if (!/^[A-Za-z0-9-]+$/.test(name)) throw new Error("bad header name " + name);
    if (/[\r\n\0]/.test(value)) throw new Error("bad header value in " + name);
    const l = name.toLowerCase();
    const i = this.headers.findIndex(([n]) => n.toLowerCase() === l);
    if (i >= 0) this.headers[i] = [this.headers[i][0], value];
    else this.headers.push([name, value]);
  }

  remove(name) {
    const l = name.toLowerCase();
    this.headers = this.headers.filter(([n]) => n.toLowerCase() !== l);
  }

  get messageId() { return this.get("Message-ID"); }
  get group() { return (this.get("Newsgroups") || "").trim(); }

  toBytes() {
    const head = this.headers.map(([n, v]) => `${n}: ${v}`).join("\r\n");
    return utf8(head + "\r\n\r\n" + this.body.replace(/\n/g, "\r\n"));
  }

  /** Parse an article as a node serves it; Path and Xref (reader-only) are dropped. */
  static fromBytes(raw) {
    const text = fromUtf8(raw).replace(/\r\n/g, "\n");
    const sep = text.indexOf("\n\n");
    if (sep < 0) throw new Error("no header/body separator");
    const art = new Article();
    for (const line of text.slice(0, sep).split("\n")) {
      const c = line.indexOf(":");
      if (c < 1) throw new Error("bad header line");
      const name = line.slice(0, c);
      if (READER_ONLY.has(name.toLowerCase())) continue;
      art.headers.push([name, line.slice(c + 1).trim()]);
    }
    art.body = normalizeBody(text.slice(sep + 2));
    return art;
  }

  canonical() {
    const items = this.headers
      .filter(([n]) => !EXCLUDED.has(n.toLowerCase()))
      .map(([n, v]) => [n.toLowerCase(), v.replace(/^[ \t]+|[ \t]+$/g, "")])
      .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0)));
    const head = items.map(([n, v]) => `${n}: ${v}\r\n`).join("");
    return utf8(head + "\r\n" + this.body.replace(/\n/g, "\r\n"));
  }

  computeMessageId() {
    const sig = sigFromText(this.get("Signature"));
    const digest = H(concat(DS("UNSEENET-MSGID-1"), this.canonical(), sig));
    return `<${b32(digest).slice(0, 32)}@unseenet>`;
  }

  /** True when the author's signature is good and the Message-ID matches (0002 section 4). */
  verify() {
    try {
      const pk = keyFromText(this.get("Author-Key") || "");
      const sig = sigFromText(this.get("Signature") || "");
      return verifySig(pk, PURPOSE_ARTICLE, this.canonical(), sig) &&
        this.messageId === this.computeMessageId();
    } catch { return false; }
  }

  /** Sign, attach postage (computed in a worker) and set the Message-ID. */
  async finalize(author, bits, onProgress) {
    this.set("Author-Key", author.keyText);
    for (const n of ["Message-ID", "Signature", "Proof-Of-Work", "Member-Signature"]) this.remove(n);
    const canonical = this.canonical();
    this.set("Signature", author.signText(PURPOSE_ARTICLE, canonical));
    this.set("Proof-Of-Work", await solvePow(canonical, bits, onProgress));
    this.set("Message-ID", this.computeMessageId());
    return this;
  }
}

export function sigFromText(text) {
  text = (text || "").trim();
  if (!text.startsWith("ed25519:")) throw new Error("unknown signature algorithm");
  const raw = b64decode(text.slice(8));
  if (raw.length !== 64) throw new Error("malformed signature");
  return raw;
}

export function newArticle(group, subject, body, { fromName, references, extra = [] } = {}) {
  const headers = [["Unseenet-Version", PROTOCOL_VERSION], ["Newsgroups", group],
    ["Date", formatDate()]];
  if (fromName != null) headers.push(["From", fromName]);
  if (subject != null) headers.push(["Subject", subject]);
  if (references && references.length) headers.push(["References", references.join(" ")]);
  return new Article(headers.concat(extra), body);
}

// --- postage (UN-RFC 0006), in a worker so the page stays responsive --------

export function solvePow(canonical, bits, onProgress) {
  return new Promise((resolve, reject) => {
    const w = new Worker(new URL("./pow-worker.js", import.meta.url), { type: "module" });
    w.onmessage = (e) => {
      if (e.data.progress != null) { onProgress?.(e.data.progress); return; }
      w.terminate();
      resolve(`sha256; bits=${bits}; nonce=${e.data.nonce}`);
    };
    w.onerror = (e) => { w.terminate(); reject(new Error(e.message || "postage failed")); };
    w.postMessage({ hc: H(canonical), bits });
  });
}

// --- manifests (UN-RFC 0004) -------------------------------------------------

/** The manifest's data if the article is a valid manifest of ``gid``, else null. */
export function manifestData(art, gid) {
  try {
    if (!art.verify() || art.group !== gid) return null;
    const [, fp] = splitGroupId(gid);
    const owner = keyFromText(art.get("Author-Key"));
    if (fingerprint(owner) !== fp) return null;
    const data = JSON.parse(art.body);
    return { ...data, owner: art.get("Author-Key") };
  } catch { return null; }
}
