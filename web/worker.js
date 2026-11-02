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
//
// And the picture worker (init.role "picture", UN-RFC 0027 section 9.2): Pyodide
// cannot start the separate process the core decodes pictures from the network
// in, so the browser's own decoder does it here, in a worker that is given
// nothing but the file - never the vault - and saves it again from its pixels
// only. It loads no Pyodide, so it costs next to nothing on a phone.
import { loadPyodide as servedPyodide } from "./pyodide/pyodide.mjs";   // FILE
const HERE = import.meta.url;                                           // FILE
let loadPyodide = servedPyodide;

const post = (m) => self.postMessage(JSON.stringify(m));
// what the loading screen says (#67): one plain sentence; the steps only as how far it is
const STARTING = "Starting Unseenet …";
const status = (text, step = null) => post({ type: "boot", text, step, of: 3 });

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
  status(STARTING, 1);
  initMessage = init;
  const py = await pyodide(init);
  status(STARTING, 2);
  await py.loadPackage(["pynacl", "aiohttp", "cryptography", "pillow", "chiavdf"],
                       { messageCallback: () => {} });   // as PYODIDE_PACKAGES in tools/build_web.py
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
  wb.start(ws, (s) => self.postMessage(s), ripening, pictures);
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

// --- pictures from the network, in a worker of their own ---
// webbackend calls pictures.decode(octets, type, ms): {bytes, type, width, height}
// or {error}. A picture that takes longer than ms ends the worker (the next one
// starts afresh), as the core kills a decoding process that takes too long.
let picturer = null;           // { worker, ready } while one runs
let pictureSeq = 0;
const pictureWaiting = new Map();      // id -> resolve
let pictureIdle = null;

function pictureWorker() {
  if (picturer) return picturer;
  const worker = new Worker(HERE, { type: "module" });
  const ready = new Promise((resolve, reject) => {
    worker.onmessage = (e) => {
      const m = e.data;
      if (m.type === "picture-ready") resolve();
      else if (m.type === "decoded") {
        const done = pictureWaiting.get(m.id);
        pictureWaiting.delete(m.id);
        if (done) done(m.error ? { error: m.error } : m.picture);
        if (!pictureWaiting.size) idleLater();
      }
    };
    worker.onerror = (e) => {
      reject(new Error(e.message || "the picture worker stopped"));
      endPictures("this picture could not be read");
    };
  });
  worker.postMessage({ type: "init", role: "picture" });
  picturer = { worker, ready };
  return picturer;
}

function endPictures(error) {
  if (picturer) picturer.worker.terminate();
  picturer = null;
  for (const done of pictureWaiting.values()) done({ error });
  pictureWaiting.clear();
}

function idleLater() {         // a worker nobody uses gives its memory back
  clearTimeout(pictureIdle);
  pictureIdle = setTimeout(() => { if (!pictureWaiting.size) endPictures(null); }, 60000);
}

const pictures = {
  async decode(octets, type, ms) {
    clearTimeout(pictureIdle);
    const id = ++pictureSeq;
    const answer = new Promise((resolve) => pictureWaiting.set(id, resolve));
    const timer = setTimeout(() => {
      if (pictureWaiting.has(id)) endPictures("this picture took too long to read");
    }, ms);
    try {
      const p = pictureWorker();
      await p.ready;
      // a copy: the worker gets this file and nothing else
      const bytes = new Uint8Array(octets);
      p.worker.postMessage({ type: "decode", id, bytes, mediaType: type }, [bytes.buffer]);
    } catch (err) {
      const done = pictureWaiting.get(id);
      pictureWaiting.delete(id);
      if (done) done({ error: "this picture could not be read" });
    }
    try { return await answer; } finally { clearTimeout(timer); }
  },
};

const MAX_PIXELS = 50_000_000;                  // as unseenet.client.decode
const SAVED_AS = { "image/jpeg": "image/jpeg", "image/webp": "image/webp" };   // else PNG

async function cleanPicture(bytes, mediaType) {
  let bmp;
  try {
    bmp = await createImageBitmap(new Blob([bytes], { type: mediaType }),
                                  { imageOrientation: "from-image" });
  } catch {
    throw new Error("this picture could not be read");
  }
  try {
    if (bmp.width * bmp.height > MAX_PIXELS) {
      throw new Error("this picture is too large to show (over 50 megapixels)");
    }
    const canvas = new OffscreenCanvas(bmp.width, bmp.height);
    canvas.getContext("2d").drawImage(bmp, 0, 0);
    // pixels only: nothing of the file but what it shows (an animation: its first frame)
    const blob = await canvas.convertToBlob({ type: SAVED_AS[mediaType] || "image/png", quality: 0.9 });
    return { bytes: new Uint8Array(await blob.arrayBuffer()), type: blob.type,
             width: bmp.width, height: bmp.height };
  } finally {
    bmp.close();
  }
}

function startPictures() {
  self.onmessage = async (e) => {
    const m = e.data;
    if (m.type !== "decode") return;
    try {
      const out = await cleanPicture(m.bytes, m.mediaType);
      self.postMessage({ type: "decoded", id: m.id, picture: out }, [out.bytes.buffer]);
    } catch (err) {
      self.postMessage({ type: "decoded", id: m.id,
        error: String(err && err.message || err) || "this picture could not be read" });
    }
  };
  self.postMessage({ type: "picture-ready" });
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
    } else if (m.type === "vault-out" || m.type === "cache-out") {
      const b = m.type === "vault-out" ? wb.vault_out() : wb.cache_out();
      const bytes = b ? b.toJs() : null;
      b?.destroy?.();
      self.postMessage({ type: "vault-file", id: m.id, bytes }, bytes ? [bytes.buffer] : []);
    } else if (m.type === "cache-in") {                     // the offline cache kept here (#68)
      wb.cache_in(new Uint8Array(m.bytes));
      post({ type: "reply", id: m.id, ok: true });
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
  if (m.type === "init" && m.role === "picture") {
    startPictures();
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
