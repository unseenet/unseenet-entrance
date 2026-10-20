// Checks the browser protocol code against real articles and the Python reference.
// Run: node tests/proto.test.mjs <raw article files...>
// Prints a signed test article (as base64) for tests/test_interop.py to check.
import { readFileSync } from "node:fs";
import { Article, Identity, manifestData, newArticle, MARKDOWN_TYPE, PURPOSE_ARTICLE } from "../web/js/article.js";
import { sha256 } from "../web/vendor/noble.js";
import { DS, b64, b32, b32decode, concat } from "../web/js/encoding.js";

let failed = 0;
const check = (name, ok) => { console.error((ok ? "ok   " : "FAIL ") + name); if (!ok) failed++; };

// base32 round trip on odd lengths
for (const n of [0, 1, 5, 16, 31, 32, 33]) {
  const data = new Uint8Array(n).map((_, i) => (i * 37 + 11) & 255);
  check(`b32 round trip ${n}`, b32decode(b32(data)).every((x, i) => x === data[i]) && b32decode(b32(data)).length === n);
}

for (const file of process.argv.slice(2)) {
  const raw = readFileSync(file);
  const art = Article.fromBytes(new Uint8Array(raw));
  check(`verify ${file}`, art.verify());
  if (art.get("Newsgroups") && file.includes("manifest")) {
    check(`manifest ${file}`, manifestData(art, art.group) !== null);
  }
  art.body += "tampered\n";
  check(`tampered ${file} fails`, !art.verify());
}

// make a post the way the web client does, with postage solved here (no Worker in node)
const id = Identity.generate();
const art = newArticle("unseenet.test@ph5fc5hop5232qsz", "Hej fra webben", "Første linje\n.punktum først\n", {
  fromName: "web-test", extra: [["Content-Type", MARKDOWN_TYPE], ["Message-Kind", "post"]],
});
art.set("Author-Key", id.keyText);
const canonical = art.canonical();
art.set("Signature", id.signText(PURPOSE_ARTICLE, canonical));
const bits = 12;
const base = concat(DS("UNSEENET-POW-1"), sha256(canonical));
for (let n = 0; ; n++) {
  const nonce = n < 256 ? new Uint8Array([n]) : new Uint8Array([n >> 8, n & 255]);
  const d = sha256(concat(base, nonce));
  const zeros = d[0] === 0 ? 8 + Math.clz32(d[1]) - 24 : Math.clz32(d[0]) - 24;
  if (zeros >= bits) { art.set("Proof-Of-Work", `sha256; bits=${bits}; nonce=${b64(nonce)}`); break; }
}
art.set("Message-ID", art.computeMessageId());
check("own article verifies", art.verify());
console.log(Buffer.from(art.toBytes()).toString("base64"));
process.exitCode = failed ? 1 : 0;
