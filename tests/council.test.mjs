// Driven by tests/test_council_js.py, on stdin:
// - {gid, cases: {name: [{author, body}]}}: what js/council.js counts per case, {seq, body, disputed};
// - {bundles: {name: bundle}, owner, group}: the urls of the list js/entrances.js takes from each.
import { countChain } from "../js/council.js";
import { verifyBundle } from "../js/entrances.js";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const req = JSON.parse(input);
const out = {};
if (req.bundles) {
  for (const [name, b] of Object.entries(req.bundles)) {
    try { out[name] = verifyBundle(b, req.owner, req.group).entrances.map((e) => e.url); }
    catch (err) { out[name] = "error: " + err.message; }
  }
} else {
  for (const [name, copies] of Object.entries(req.cases)) {
    const ch = countChain(req.gid, copies);
    out[name] = { seq: ch.current ? ch.current.seq : null, body: ch.current ? ch.current.body : null,
      disputed: ch.disputed };
  }
}
process.stdout.write(JSON.stringify(out));
