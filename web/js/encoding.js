// Encodings and helpers from UN-RFC 0003 section 1.
import { sha256 } from "../vendor/noble.js";

const enc = new TextEncoder();
const dec = new TextDecoder("utf-8", { fatal: false });

export const utf8 = (s) => enc.encode(s);
export const fromUtf8 = (b) => dec.decode(b);

export function concat(...parts) {
  const n = parts.reduce((a, p) => a + p.length, 0);
  const out = new Uint8Array(n);
  let i = 0;
  for (const p of parts) { out.set(p, i); i += p.length; }
  return out;
}

const B32 = "abcdefghijklmnopqrstuvwxyz234567";

/** RFC 4648 base32, lowercase, no padding. */
export function b32(data) {
  let bits = 0, value = 0, out = "";
  for (const byte of data) {
    value = ((value << 8) | byte) & 0xffff; bits += 8;
    while (bits >= 5) { out += B32[(value >>> (bits - 5)) & 31]; bits -= 5; }
  }
  if (bits > 0) out += B32[(value << (5 - bits)) & 31];
  return out;
}

export function b32decode(text) {
  const out = [];
  let bits = 0, value = 0;
  for (const ch of text.toLowerCase()) {
    const v = B32.indexOf(ch);
    if (v < 0) throw new Error("bad base32");
    value = ((value << 5) | v) & 0xffff; bits += 5;
    if (bits >= 8) { out.push((value >>> (bits - 8)) & 255); bits -= 8; }
  }
  return new Uint8Array(out);
}

/** RFC 4648 base64url, no padding. */
export function b64(data) {
  let s = "";
  for (const byte of data) s += String.fromCharCode(byte);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64decode(text) {
  let t = text.replace(/-/g, "+").replace(/_/g, "/");
  t += "=".repeat((4 - (t.length % 4)) % 4);
  const s = atob(t);
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}

/** SHA-256. */
export const H = (data) => sha256(data);

/** Domain separator: ASCII label followed by a zero octet. */
export const DS = (label) => concat(utf8(label), new Uint8Array([0]));

export const equalBytes = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
