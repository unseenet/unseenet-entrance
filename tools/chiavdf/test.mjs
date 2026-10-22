// The chiavdf wheel in Pyodide gives the proofs native chiavdf gives, and they verify.
//
//   node tools/chiavdf/test.mjs WHEEL [CORE_SRC]
//
// Needs `npm install pyodide@<the version in build.sh>`. With CORE_SRC (unseenet's
// src/), also runs the core's own ripening self-check on the wheel.
import { loadPyodide } from "pyodide";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const [wheel, core] = process.argv.slice(2);
const vectors = fileURLToPath(new URL("./vectors.json", import.meta.url));
const py = await loadPyodide();
if (core) await py.loadPackage(["pynacl"], { messageCallback: () => {} });
py.unpackArchive(new Uint8Array(readFileSync(wheel)), "wheel");
py.globals.set("VECTORS", readFileSync(vectors, "utf8"));
if (core) {
  py.FS.mkdirTree("/core");
  py.FS.mount(py.FS.filesystems.NODEFS, { root: core }, "/core");
}
const failed = py.runPython(`
import json, sys, time
import chiavdf

data = json.loads(VECTORS)
bits = data["discriminant_bits"]
failed = 0
for v in data["vectors"]:
    seed, x, t = bytes.fromhex(v["seed"]), bytes.fromhex(v["x"]), v["t"]
    got = chiavdf.prove(seed, x, bits, t, "")
    same = got.hex() == v["result"]
    ok = chiavdf.verify_n_wesolowski(chiavdf.create_discriminant(seed, bits), x, got, t, bits, 0)
    print(f"t={t}: {'same as native' if same else 'DIFFERENT'}, {'verifies' if ok else 'DOES NOT VERIFY'}")
    failed += (not same) + (not ok)

t = 1 << 18
start = time.time()
chiavdf.prove(b"unseenet-speed", bytes([8]) + bytes(99), bits, t, "")
print(f"speed: {t / (time.time() - start):,.0f} iterations/s")

if ${core ? "True" : "False"}:
    sys.path.insert(0, "/core")
    from unseenet.core import ripening
    problem = ripening.available() and ripening.self_check()
    print("core self-check:", problem or "ok" if ripening.available() else "chiavdf not found")
    failed += bool(problem) or not ripening.available()
failed
`);
process.exit(failed ? 1 : 0);
