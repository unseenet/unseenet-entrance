// The signed list of web entrances (UN-RFC 0043 sections 8.1-8.2), checked here.
// A bundle is what an entrance serves at /.well-known/unseenet-entrances.json:
// the list group's manifest and its newest posts, raw and base64. Nothing in it
// is trusted until its signatures check out against the pinned owner key.
import { Article, keyFromText, manifestData, splitGroupId, fingerprint } from "./article.js";
import { b64decode } from "./encoding.js";

export const PROJECT_KEY = "ed25519:det5dxevwbk36eds2indrzlazyri3fq3bgdzw6kx4ems3o267sya";
export const PROJECT_GROUP = "unseenet.entrances@ph5fc5hop5232qsz";

const ONION = /^[a-z2-7]{56}\.onion$/;

/** Entries of a version 1 list, as UN-RFC 0043 8.1 defines them. ``strict``: https only. */
export function parseList(text, strict = true) {
  const data = JSON.parse(text);
  if (!data || data.v !== 1 || !Array.isArray(data.entrances)) throw new Error("not a version 1 entrance list");
  const out = [];
  for (const item of data.entrances.slice(0, 200)) {
    if (!item || typeof item !== "object") continue;
    let url = String(item.url || "").trim();
    let onion = String(item.onion || "").trim().toLowerCase();
    const okUrl = strict ? /^https:\/\/[^/\s]+\/?$/ : /^https?:\/\/[^/\s]+\/?$/;
    if (url && !okUrl.test(url)) url = "";
    if (url && !url.endsWith("/")) url += "/";
    if (onion && !ONION.test(onion)) onion = "";
    if (!url && !onion) continue;
    out.push({ name: String(item.name || url || onion).slice(0, 64), url, onion,
      operator: String(item.operator || "").slice(0, 80), contact: String(item.contact || "").slice(0, 120),
      description: String(item.description || "").slice(0, 200) });
  }
  if (!out.length) throw new Error("the list has no usable entrances");
  return { updated: String(data.updated || "").slice(0, 32), entrances: out };
}

/**
 * The posts of a bundle whose signatures check out against a pinned owner:
 * [{art, date, signer}], or throws, saying why. ``what`` names the group in messages.
 */
export function signedPosts(bundle, ownerKey, group, what = "list") {
  if (!bundle || bundle.group !== group) throw new Error(`not the ${what} this page trusts`);
  if (!bundle.manifest) throw new Error(`the entrance has no copy of the ${what} yet`);
  const [, fp] = splitGroupId(group);
  if (fingerprint(keyFromText(ownerKey)) !== fp) throw new Error("pinned key and group do not match");
  const m = manifestData(Article.fromBytes(b64decode(bundle.manifest)), group);
  if (!m) throw new Error(`the ${what}'s group settings are not signed by its owner`);
  if (m.owner !== ownerKey) throw new Error(`the ${what}'s group has another owner`);
  const signers = new Set([ownerKey, ...(m.posters || [])]);
  const out = [];
  for (const raw of bundle.posts || []) {
    let art;
    try { art = Article.fromBytes(b64decode(raw)); } catch { continue; }
    if (art.group !== group || !art.verify() || !signers.has(art.get("Author-Key"))) continue;
    out.push({ art, date: new Date(art.get("Date") || 0), signer: art.get("Author-Key") });
  }
  return out;
}

/**
 * Check a bundle against a pinned owner. Returns the newest valid list:
 * {updated, entrances, date, signer} - or throws, saying why.
 */
export function verifyBundle(bundle, ownerKey = PROJECT_KEY, group = PROJECT_GROUP) {
  const strict = group === PROJECT_GROUP;
  let best = null;
  for (const { art, date, signer } of signedPosts(bundle, ownerKey, group)) {
    let list;
    try { list = parseList(art.body, strict); } catch { continue; }
    if (!best || date > best.date) best = { ...list, date, signer };
  }
  if (!best) throw new Error("no post in the list is signed by its owner or a poster");
  return best;
}
