// Signed versions of the web client (UN-RFC 0043 sections 7.2 and 7.3), checked here.
// A bundle is what an entrance serves at /.well-known/unseenet-releases.json: the
// group's manifest and its newest posts, raw. Each valid post names a version and
// the SHA-256 of every file; files are kept only if they match, wherever they came from.
import { PROJECT_KEY, signedPosts } from "./entrances.js";

export const RELEASES_GROUP = "unseenet.releases@ph5fc5hop5232qsz";

const VERSION = /^\d{1,10}(\.\d{1,10}){0,5}$/;
const PATH = /^(?!.*\.\.)(?!\/)[A-Za-z0-9._\-/]{1,200}$/;
const NEEDED = ["index.html", "sw.js", "worker.js", "shim.js", "version.json"];

/** -1, 0 or 1: versions are dot-separated numbers, compared part by part. */
export function compareVersions(a, b) {
  const x = String(a).split(".").map(Number), y = String(b).split(".").map(Number);
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    const d = (x[i] || 0) - (y[i] || 0);
    if (d) return d < 0 ? -1 : 1;
  }
  return 0;
}

/** A release post's body: {version, files: {path: sha256}, zip: {name, sha256}, built}. */
export function parseRelease(text) {
  const lines = String(text).split(/\r?\n/);
  const head = /^Unseenet web client (\S+)\s*$/.exec(lines[0] || "");
  if (!head || !VERSION.test(head[1])) throw new Error("not a release of the web client");
  const version = head[1];
  const files = {};
  let zip = null, built = "";
  for (const line of lines.slice(1)) {
    const m = /^([0-9a-f]{64}) [ *]?(\S+)$/.exec(line);
    if (!m) { if (/^Built from /.test(line)) built = line.slice(11, 300); continue; }
    const [, hash, path] = m;
    if (!PATH.test(path)) throw new Error("a file name in the release is not allowed: " + path.slice(0, 60));
    if (path === `unseenet-web-${version}.zip`) zip = { name: path, sha256: hash };
    else files[path] = hash;
  }
  for (const n of NEEDED) if (!files[n]) throw new Error("the release has no " + n);
  return { version, files, zip, built };
}

/**
 * The valid releases in a bundle, newest version first:
 * [{version, files, zip, built, date, signer, byOwner}] - or throws, saying why.
 */
export function verifyReleases(bundle, ownerKey = PROJECT_KEY, group = RELEASES_GROUP) {
  const out = new Map();
  for (const { art, date, signer } of signedPosts(bundle, ownerKey, group, "list of versions")) {
    let r;
    try { r = parseRelease(art.body); } catch { continue; }
    if (!out.has(r.version)) out.set(r.version, { ...r, date, signer, byOwner: signer === ownerKey });
  }
  return [...out.values()].sort((a, b) => compareVersions(b.version, a.version));
}
