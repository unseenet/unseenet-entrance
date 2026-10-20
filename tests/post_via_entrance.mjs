// Post through a running entrance from node (no browser), to tell browser and protocol apart.
//   node --experimental-default-type=module tests/post_via_entrance.mjs http://127.0.0.1:8043/ <onion> <group>
import { Connection } from "../web/js/nntp.js";
import { Identity, MARKDOWN_TYPE, PURPOSE_ARTICLE, newArticle } from "../web/js/article.js";
import { sha256 } from "../web/vendor/noble.js";
import { DS, b64, concat } from "../web/js/encoding.js";

const [base, onion, group] = process.argv.slice(2);
const c = await Connection.open(base, onion);
const id = Identity.generate();
const art = newArticle(group, "Test via entrance (node)", "Posted through a web entrance from node.\n", {
  fromName: "web-entrance-test", extra: [["Content-Type", MARKDOWN_TYPE], ["Message-Kind", "post"]],
});
art.set("Author-Key", id.keyText);
const canonical = art.canonical();
art.set("Signature", id.signText(PURPOSE_ARTICLE, canonical));
const bits = 20;
const pre = concat(DS("UNSEENET-POW-1"), sha256(canonical));
for (let n = 0; ; n++) {
  const nonce = new Uint8Array([(n >> 16) & 255, (n >> 8) & 255, n & 255]).slice(n < 256 ? 2 : n < 65536 ? 1 : 0);
  const d = sha256(concat(pre, nonce));
  let z = 0; for (const b of d) { if (b === 0) { z += 8; continue; } z += Math.clz32(b) - 24; break; }
  if (z >= bits) { art.set("Proof-Of-Work", `sha256; bits=${bits}; nonce=${b64(nonce)}`); break; }
}
art.set("Message-ID", art.computeMessageId());
console.log("local verify:", art.verify(), art.messageId);
try { console.log("posted:", await c.post(art.toBytes())); } catch (e) { console.log("refused:", e.message); }
c.close();
