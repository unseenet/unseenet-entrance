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
//
// The same file is also the ripening worker (init.role "ripen", #27): the
// backend starts one to prove key-ripening segments (UN-RFC 0025), which take
// minutes to hours. Pyodide has no threads, so a segment in the backend would
// stop everything else; in a worker of its own it doesn't, and stopping it is
// terminating that worker.
import { loadPyodide as servedPyodide } from "./pyodide/pyodide.mjs";   // FILE
const HERE = import.meta.url;                                           // FILE
let loadPyodide = servedPyodide;

const post = (m) => self.postMessage(JSON.stringify(m));
const status = (text) => post({ type: "boot", text });

let wb = null;
let started = null;
const queue = [];

let initMessage = null;               // kept to start the ripening worker the same way

async function pyodide(init) {
  let opts;
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
  }
  return loadPyodide(opts);
}

async function start(init) {
  status("Loading Python ...");
  initMessage = init;
  const py = await pyodide(init);
  status("Loading the Unseenet core ...");
  await py.loadPackage(["pynacl", "aiohttp", "chiavdf"], { messageCallback: () => {} });
  let bundle = init.files && init.files["bundle.zip"];
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
  wb.start(ws, (s) => self.postMessage(s), ripening);
  post({ type: "ready" });
}

// --- key ripening in a worker of its own ---
// webbackend calls ripening.prove(seed, x, bits, t) and ripening.stop();
// prove gives the result's bytes, or null when stopped.
let ripener = null;            // { worker, ready } while one runs
let pending = null;            // { key, resolve, done } of the segment being proved

function ripenerWorker() {
  if (ripener) return ripener;
  const worker = new Worker(HERE, { type: "module" });
  const ready = new Promise((resolve, reject) => {
    worker.onmessage = (e) => {
      const m = e.data;
      if (m.type === "ripen-ready") resolve();
      else if (m.type === "ripen-fatal") reject(new Error(m.text));
      else if (m.type === "proved" && pending) finish(m.bytes);
    };
    worker.onerror = (e) => {
      reject(new Error(e.message || "the ripening worker stopped"));
      ripening.stop();
    };
  });
  const { files, ...rest } = initMessage;
  // the client in a file: the ripening worker gets its own copy of Pyodide's files
  worker.postMessage({ ...rest, type: "init", role: "ripen", files: files && { ...files, "bundle.zip": undefined } });
  ripener = { worker, ready };
  return ripener;
}

function finish(bytes) {
  const p = pending;
  pending = null;
  p.resolve(bytes);
}

const hex = (a) => Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");

const ripening = {
  // The core may ask for the same segment twice at once (posting waits for
  // level 0 while the background ripens on): both get the one proof. Another
  // segment waits for the one being proved.
  async prove(seed, x, bits, t) {
    const key = `${hex(seed)}/${hex(x)}/${bits}/${t}`;
    while (pending && pending.key !== key) await pending.done;
    if (pending) return pending.done;
    let resolve;
    const done = new Promise((r) => { resolve = r; });
    pending = { key, resolve, done };
    try {
      const r = ripenerWorker();
      await r.ready;
      r.worker.postMessage({ type: "prove", seed, x, bits, t });
    } catch (err) {
      console.warn("key ripening is not available here:", err && err.message || err);
      ripening.stop();
    }
    return done;
  },
  stop() {                     // the run file is gone, or the page stops ripening
    if (ripener) ripener.worker.terminate();
    ripener = null;
    if (pending) finish(null);
  },
};

async function startRipener(init) {
  try {
    const py = await pyodide(init);
    await py.loadPackage(["chiavdf"], { messageCallback: () => {} });
    // the arguments come as Uint8Arrays, chiavdf wants bytes
    const prove = py.runPython(`
import chiavdf
from pyodide.ffi import to_js
def prove(seed, x, bits, t):
    return to_js(chiavdf.prove(seed.to_bytes(), x.to_bytes(), int(bits), int(t), ""))
prove`);
    self.onmessage = (e) => {
      const m = e.data;
      if (m.type !== "prove") return;
      const bytes = prove(m.seed, m.x, m.bits, m.t);
      self.postMessage({ type: "proved", bytes }, [bytes.buffer]);
    };
    self.postMessage({ type: "ripen-ready" });
  } catch (err) {
    self.postMessage({ type: "ripen-fatal", text: String(err && err.message || err) });
  }
}

async function handle(m) {
  try {
    if (m.type === "api") {
      const out = await wb.handle(m.method, m.path, m.body ?? null);
      post({ type: "api", id: m.id, ...JSON.parse(out) });
    } else if (m.type === "vault-in") {
      wb.vault_in(new Uint8Array(m.bytes));
      post({ type: "reply", id: m.id, ok: true });
    } else if (m.type === "entrance") {
      wb.switch_entrance(m.url);
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
  if (m.type === "init" && m.role === "ripen") {
    startRipener(m);
    return;
  }
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
