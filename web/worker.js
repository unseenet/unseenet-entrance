// The graphical client's backend in a Web Worker (UN-RFC 0043 section 6):
// Pyodide runs the real unseenet core and unseenet-gui's own server code.
// Everything is loaded from this entrance; nothing from anywhere else.
import { loadPyodide } from "./pyodide/pyodide.mjs";

const status = (text) => self.postMessage(JSON.stringify({ type: "boot", text }));

let wb = null;
const ready = (async () => {
  status("Loading Python ...");
  const py = await loadPyodide({ indexURL: new URL("./pyodide/", import.meta.url).href });
  status("Loading the Unseenet core ...");
  await py.loadPackage(["pynacl", "aiohttp"], { messageCallback: () => {} });
  const bundle = await (await fetch(new URL("./py/bundle.zip", import.meta.url))).arrayBuffer();
  py.unpackArchive(bundle, "zip", { extractDir: "/app" });
  py.runPython(`import sys; sys.path.insert(0, "/app")`);
  wb = py.pyimport("webbackend");
  const ws = new URL("./onntp", import.meta.url);
  ws.protocol = ws.protocol === "https:" ? "wss:" : "ws:";
  wb.start(ws.href, (s) => self.postMessage(s));
  self.postMessage(JSON.stringify({ type: "ready" }));
  return py;
})().catch((err) => {
  self.postMessage(JSON.stringify({ type: "fatal", text: String(err && err.message || err) }));
  throw err;
});

self.onmessage = async (e) => {
  const m = e.data;
  try {
    await ready;
    if (m.type === "api") {
      const out = await wb.handle(m.method, m.path, m.body ?? null);
      self.postMessage(JSON.stringify({ type: "api", id: m.id, ...JSON.parse(out) }));
    } else if (m.type === "vault-in") {
      wb.vault_in(new Uint8Array(m.bytes));
      self.postMessage(JSON.stringify({ type: "reply", id: m.id, ok: true }));
    } else if (m.type === "vault-out") {
      const b = wb.vault_out();
      const bytes = b ? b.toJs() : null;
      b?.destroy?.();
      self.postMessage({ type: "vault-file", id: m.id, bytes }, bytes ? [bytes.buffer] : []);
    }
  } catch (err) {
    const text = String(err && err.message || err).split("\n").filter(Boolean).pop();
    self.postMessage(JSON.stringify({ type: m.type === "api" ? "api" : "reply", id: m.id,
      status: 500, type2: "error", body: JSON.stringify({ error: text }), ok: false, error: text }));
  }
};
