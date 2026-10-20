// Proof-of-work postage (UN-RFC 0006): find a nonce so that
// SHA-256(DS("UNSEENET-POW-1") || H(canonical) || nonce) has `bits` leading zero bits.
import { sha256 } from "../vendor/noble.js";
import { DS, b64, concat } from "./encoding.js";

function leadingZeroBits(d) {
  let n = 0;
  for (const byte of d) {
    if (byte === 0) { n += 8; continue; }
    return n + Math.clz32(byte) - 24;
  }
  return n;
}

function nonceBytes(counter) {
  // big-endian, as few octets as possible, at least one (as the reference client)
  const out = [];
  do { out.unshift(counter % 256); counter = Math.floor(counter / 256); } while (counter > 0);
  return new Uint8Array(out);
}

self.onmessage = (e) => {
  const { hc, bits } = e.data;
  const base = sha256.create().update(concat(DS("UNSEENET-POW-1"), hc));
  const expected = 2 ** bits;
  for (let counter = 0; ; counter++) {
    const nonce = nonceBytes(counter);
    if (leadingZeroBits(base.clone().update(nonce).digest()) >= bits) {
      self.postMessage({ nonce: b64(nonce) });
      return;
    }
    if (counter % 50000 === 0) self.postMessage({ progress: Math.min(0.99, counter / (expected * 2)) });
  }
};
