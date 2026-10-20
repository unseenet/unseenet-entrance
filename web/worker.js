// The graphical client's backend in a Web Worker (UN-RFC 0043 section 6):
// Pyodide runs the real unseenet core and unseenet-gui's own server code.
//
// Two ways to start, decided by the page's first message ("init"):
//  - served by an entrance: Pyodide and the Python bundle come from the entrance;
//  - the client in a file (section 8.7): everything comes in the message, from
//    the file itself, and the entrance is only used as a tunnel.
// Either way nothing is loaded from anywhere else.
// The client in a file starts this from a data: URL (browsers refuse module
// workers from blobs made by a file on disk); it replaces the two marked lines.
import { loadPyodide as servedPyodide } from "./pyodide/pyodide.mjs";   // FILE
const HERE = import.meta.url;                                           // FILE
let loadPyodide = servedPyodide;

const post = (m) => self.postMessage(JSON.stringify(m));
const status = (text) => post({ type: "boot", text });

let wb = null;
let started = null;
const queue = [];

async function start(init) {
  status("Loading Python ...");
  let opts;
  let bundle;
  if (!init.files) {
    opts = { indexURL: new URL("./pyodide/", HERE).href };
  } else {
    // the client in a file: serve Pyodide's own requests from what the page handed over
    const base = "https://unseenet.invalid/pyodide/";
    const files = init.files;
    const realFetch = self.fetch.bind(self);
    self.fetch = async (input, o) => {
      const url = typeof input === "string" ? input : (input.url || String(input));
      if (url.startsWith(base)) {
        const name = url.slice(base.length).split("?")[0];
        if (!files[name]) return new Response("not in this file", { status: 404 });
        const type = name.endsWith(".wasm") ? "application/wasm" : "application/octet-stream";
        return new Response(files[name], { headers: { "Content-Type": type } });
      }
      return realFetch(input, o);
    };
    const blob = (n) => URL.createObjectURL(new Blob([files[n]], { type: "text/javascript" }));
    loadPyodide = (await import(blob("pyodide.mjs"))).loadPyodide;
    const asm = await import(blob("pyodide.asm.mjs"));
    opts = { indexURL: base, packageBaseUrl: base, createPyodideModule: asm.default,
      lockFileContents: JSON.parse(new TextDecoder().decode(files["pyodide-lock.json"])) };
    bundle = files["bundle.zip"];
  }
  const py = await loadPyodide(opts);
  status("Loading the Unseenet core ...");
  await py.loadPackage(["pynacl", "aiohttp"], { messageCallback: () => {} });
  if (!bundle) bundle = await (await fetch(new URL("./py/bundle.zip", HERE))).arrayBuffer();
  py.unpackArchive(bundle, "zip", { extractDir: "/app" });
  py.runPython(`import sys; sys.path.insert(0, "/app")`);
  wb = py.pyimport("webbackend");
  let ws = init.entrance;
  if (!ws) {
    const u = new URL("./onntp", HERE);
    u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
    ws = u.href;
  }
  wb.start(ws, (s) => self.postMessage(s));
  post({ type: "ready" });
}

async function handle(m) {
  try {
    if (m.type === "api") {
      const out = await wb.handle(m.method, m.path, m.body ?? null);
      post({ type: "api", id: m.id, ...JSON.parse(out) });
    } else if (m.type === "vault-in") {
      wb.vault_in(new Uint8Array(m.bytes));
      post({ type: "reply", id: m.id, ok: true });
    } else if (m.type === "vault-out") {
      const b = wb.vault_out();
      const bytes = b ? b.toJs() : null;
      b?.destroy?.();
      self.postMessage({ type: "vault-file", id: m.id, bytes }, bytes ? [bytes.buffer] : []);
    }
  } catch (err) {
    const text = String(err && err.message || err).split("\n").filter(Boolean).pop();
    post({ type: m.type === "api" ? "api" : "reply", id: m.id, status: 500,
      body: JSON.stringify({ error: text }), ok: false, error: text });
  }
}

self.onmessage = async (e) => {
  const m = e.data;
  if (m.type === "init") {
    started = start(m).then(async () => {
      for (const q of queue.splice(0)) await handle(q);    // asked for before we were ready
    }).catch((err) => {
      post({ type: "fatal", text: String(err && err.message || err) });
      throw err;
    });
    return;
  }
  if (!started) { queue.push(m); return; }
  try { await started; } catch { return; }
  await handle(m);
};
