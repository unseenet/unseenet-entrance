// Council groups (UN-RFC 0054 Part I) and the lists a list council keeps (UN-RFC 0020):
// counting a council's chain of manifests from the identical copies its members posted,
// as unseenet's core/council.py does, so that a browser can check a list the council's
// replicas posted with no key pinned but the council's Group ID - which the genesis body
// itself pins. Nothing here is trusted before its signatures check out.
import { keyFromText, keyToText } from "./article.js";
import { DS, H, b32, b64, concat, utf8 } from "./encoding.js";

const DS_COUNCIL = "UNSEENET-COUNCIL-1";
const TYPES = ["open", "channel", "closed"];
const MAX_MEMBERS = 16;
const MIN_FULL = 2;
const DAY = 86400e3;
const ADMISSION_GAP = 30 * DAY;
const PROBATION = 180 * DAY;
const FOUNDER = "founder";
const FOUNDER_GAP = 7 * DAY;
const FOUNDER_PROBATION = 30 * DAY;
const FOUNDING_MAX = 3 * 366 * DAY;
const EXTEND_MAX = 366 * DAY;

/** JSON as unseenet writes it: keys sorted, no spaces, non-ASCII as is. */
export function canonicalJson(v) {
  if (Array.isArray(v)) return "[" + v.map(canonicalJson).join(",") + "]";
  if (v && typeof v === "object") {
    return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + canonicalJson(v[k])).join(",") + "}";
  }
  return JSON.stringify(v);
}

const text = (body) => (body.endsWith("\r\n") ? body.slice(0, -2) : body.replace(/\n+$/, ""));
export const councilFingerprint = (body) => b32(H(concat(DS(DS_COUNCIL), utf8(text(body))))).slice(0, 16);
export const bodyHash = (body) => b64(H(concat(DS(DS_COUNCIL), utf8(text(body)))));

function utcDate(v) {
  if (typeof v !== "string" || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$/.test(v)) throw new Error("not an RFC 3339 UTC date");
  const t = Date.parse(v);
  if (Number.isNaN(t)) throw new Error("not a date");
  return t;
}

const int = (c, k, dflt) => {
  const v = c[k] === undefined ? dflt : c[k];
  if (!Number.isInteger(v)) throw new Error(`${k} is an integer`);
  return v;
};
const keyText = (k) => keyToText(keyFromText(k));

/** A council manifest from its body (UN-RFC 0054 section 4), or throws. */
export function councilManifest(groupId, body) {
  const t = body.replace(/\n+$/, "").replace(/\r$/, "");
  const data = JSON.parse(t);
  if (!data || typeof data !== "object" || Array.isArray(data) || canonicalJson(data) !== t) throw new Error("not canonical JSON");
  const at = groupId.lastIndexOf("@");
  const c = data.council;
  if (!c || typeof c !== "object") throw new Error("no council");
  const members = (c.members || []).map((m) => {
    if (!m || typeof m.full !== "boolean") throw new Error("bad member");
    if (m.by !== undefined && m.by !== FOUNDER) throw new Error("a member's by is founder or absent");
    return { key: keyText(m.key), joined: utcDate(m.joined), full: m.full, by: m.by || null };
  });
  if (!Number.isInteger(c.seq) || c.seq < 1) throw new Error("bad seq");
  let founding = null;
  if (c.founding !== undefined) {
    const f = c.founding;
    const size = f.size, mp = f.max_probation === undefined ? 2 : f.max_probation, ren = f.renounced === undefined ? false : f.renounced;
    if (!Number.isInteger(size) || !Number.isInteger(mp) || size < 1 || size > MAX_MEMBERS || mp < 0 || typeof ren !== "boolean") throw new Error("bad founding");
    founding = { founder: keyText(f.founder), until: utcDate(f.until), size, maxProbation: mp, renounced: ren };
  }
  const m = {
    groupId, name: data.name, type: data.type, seq: c.seq, prev: c.prev === undefined ? null : c.prev,
    date: utcDate(c.date), members, minLevel: int(c, "min_level", 4), maxProbation: int(c, "max_probation", 1),
    sponsors: int(c, "sponsors", 0), succeeds: c.succeeds === undefined ? null : c.succeeds,
    body: t, data, founding, room: c.room === undefined ? null : c.room,
  };
  if ("owner" in data) throw new Error("a council manifest has no owner");
  if (data.v !== 1 || m.name !== groupId.slice(0, at) || !TYPES.includes(m.type)) throw new Error("bad council manifest fields");
  if (data.serial !== m.seq) throw new Error("serial is council.seq");
  const keys = members.map((x) => x.key);
  if (!members.length || members.length > MAX_MEMBERS || new Set(keys).size !== keys.length) throw new Error("1 to 16 distinct members");
  if ((m.seq === 1) !== (m.prev === null) || (m.prev !== null && typeof m.prev !== "string")) throw new Error("prev from seq 2, and only then");
  if (m.seq !== 1 && m.succeeds !== null) throw new Error("succeeds is for the genesis only");
  if (m.minLevel < 0 || m.maxProbation < 0 || m.sponsors < 0) throw new Error("negative numbers");
  if (m.seq === 1 && founding) {
    if (founding.until > m.date + FOUNDING_MAX || founding.renounced) throw new Error("a founding period lasts at most 3 years");
    if (members.some((x) => x.by)) throw new Error("the genesis members were not admitted");
  }
  m.keys = keys;
  m.full = members.filter((x) => x.full).map((x) => x.key);
  m.onProbation = members.filter((x) => !x.full).map((x) => x.key);
  m.hash = bodyHash(t);
  return m;
}

const foundingLasts = (m, at) => {
  const f = m.founding;
  return !!f && !f.renounced && at < f.until && m.full.length < f.size;
};
const sameMember = (a, b) => a.key === b.key && a.joined === b.joined && a.full === b.full && a.by === b.by;
const sameFounding = (a, b) => (a === null && b === null) || (a && b && a.founder === b.founder &&
  a.until === b.until && a.size === b.size && a.maxProbation === b.maxProbation && a.renounced === b.renounced);

function foundingChange(prev, m, membersChange) {
  const a = prev.founding, b = m.founding;
  if (sameFounding(a, b)) return null;
  if (!a || !b) return "the founding period is set at the genesis";
  if (membersChange) return "the founding period changes only in a manifest that changes no member";
  if (a.renounced) return "a renounced founding period stays renounced";
  const same = a.founder === b.founder && a.size === b.size && a.maxProbation === b.maxProbation;
  if (same && b.renounced && b.until === a.until) return null;
  if (same && !b.renounced && b.until !== a.until) {
    if (foundingLasts(prev, m.date)) return "the founding period is extended only once it is over";
    if (!(m.date < b.until && b.until <= m.date + EXTEND_MAX)) return "an extension sets until to at most a year after its date";
    return null;
  }
  return "the founding period changes only by renouncing or extending";
}

/** UN-RFC 0054 section 5, items 2 and 3: why ``m`` may not follow ``prev``, or null. */
export function changeOk(prev, m, lastAdmission) {
  if (m.date < prev.date) return "date earlier than the previous manifest's";
  const old = new Map(prev.members.map((x) => [x.key, x]));
  const neu = new Map(m.members.map((x) => [x.key, x]));
  const added = [...neu.keys()].filter((k) => !old.has(k));
  const removed = [...old.keys()].filter((k) => !neu.has(k));
  const changed = [...neu.keys()].filter((k) => old.has(k) && !sameMember(old.get(k), neu.get(k)));
  if (added.length + removed.length + changed.length > 1) return "more than one change of members";
  if (m.room !== prev.room) return "the room is set at the genesis";
  const why = foundingChange(prev, m, added.length + removed.length + changed.length > 0);
  if (why) return why;
  if (added.length) {
    const x = neu.get(added[0]);
    if (x.full || x.joined !== m.date) return "an admitted member starts on probation";
    if (x.by === FOUNDER) {
      if (!foundingLasts(prev, m.date)) return "a founder admission only while the founding period lasts";
      if (m.date < lastAdmission + FOUNDER_GAP) return "less than 7 days since the last admission";
      if (prev.onProbation.length >= prev.founding.maxProbation) return "too many on probation";
    } else {
      if (m.date < lastAdmission + ADMISSION_GAP) return "less than 30 days since the last admission";
      if (prev.onProbation.length >= prev.maxProbation) return "too many on probation";
    }
  }
  if (changed.length) {
    const a = old.get(changed[0]), b = neu.get(changed[0]);
    if (!(!a.full && b.full && a.joined === b.joined && a.by === b.by)) return "a member's fields change only by promotion";
    if (m.date < a.joined + (a.by === FOUNDER ? FOUNDER_PROBATION : PROBATION)) return "promotion too early";
  }
  if (m.full.length < MIN_FULL) return "fewer than 2 full members left";
  return null;
}

export const quorum = (eligible) => Math.max(2, Math.floor(eligible / 2) + 1);

/**
 * UN-RFC 0054 section 5: the chain that ``copies`` - [{author, body}] of every council
 * manifest article held for ``groupId``, their signatures already checked - make count.
 * Returns {counted, current, disputed}.
 */
export function countChain(groupId, copies) {
  const bySeq = new Map();
  for (const { author, body } of copies) {
    let m;
    try { m = councilManifest(groupId, body); } catch { continue; }
    if (!bySeq.has(m.seq)) bySeq.set(m.seq, new Map());
    const slot = bySeq.get(m.seq);
    if (!slot.has(m.body)) slot.set(m.body, { m, signed: new Set() });
    try { slot.get(m.body).signed.add(keyText(author)); } catch { /* not a key */ }
  }
  const chain = { counted: [], current: null, disputed: false };
  const fp = groupId.slice(groupId.lastIndexOf("@") + 1);
  const genesis = [...(bySeq.get(1) || new Map()).values()].filter(({ m, signed }) =>
    councilFingerprint(m.body) === fp && m.members.length >= MIN_FULL &&
    m.members.every((x) => x.full && x.joined === m.date) && m.keys.every((k) => signed.has(k)));
  if (!genesis.length) return chain;
  chain.counted.push(genesis[0].m);
  let lastAdmission = genesis[0].m.date;
  for (let seq = 2; bySeq.has(seq); seq++) {
    const prev = chain.counted[chain.counted.length - 1];
    const winners = [];
    for (const { m, signed } of bySeq.get(seq).values()) {
      if (m.prev !== prev.hash || m.type !== prev.type || m.name !== prev.name) continue;
      if (changeOk(prev, m, lastAdmission) !== null) continue;
      const eligible = prev.full.filter((k) => m.keys.includes(k));
      const got = eligible.filter((k) => signed.has(k));
      if (got.length >= quorum(eligible.length)) winners.push(m);
    }
    if (!winners.length) break;
    if (winners.length > 1) { chain.disputed = true; break; }
    const m = winners[0];
    if (m.keys.some((k) => !prev.keys.includes(k))) lastAdmission = m.date;
    chain.counted.push(m);
  }
  chain.current = chain.counted[chain.counted.length - 1];
  return chain;
}

/** (council seq, day) of a list's ``updated`` (UN-RFC 0020 section 8), or null. */
export function updatedDay(updated) {
  const m = /^council (\d+) day (\d{4}-\d\d-\d\d)$/.exec(String(updated || ""));
  return m ? { seq: Number(m[1]), day: m[2] } : null;
}
