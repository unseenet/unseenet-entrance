// ONNTP (UN-RFC 0005) spoken through a web entrance (UN-RFC 0043 section 4):
// a WebSocket whose bytes go, unchanged, to a node's onion service over Tor.
import { concat, utf8, fromUtf8 } from "./encoding.js";

export class NNTPError extends Error {
  constructor(code, text) { super(`${code} ${text}`); this.code = code; this.text = text; }
}

export class Connection {
  constructor(ws) {
    this.ws = ws;
    this.buf = new Uint8Array(0);
    this.waiters = [];
    this.closed = false;
    this.queue = Promise.resolve();          // one command at a time
    ws.binaryType = "arraybuffer";
    ws.onmessage = (e) => {
      const data = typeof e.data === "string" ? utf8(e.data) : new Uint8Array(e.data);
      this.buf = concat(this.buf, data);
      this._wake();
    };
    ws.onclose = (e) => { this.closed = true; this.closeReason = e.reason || `closed (${e.code})`; this._wake(); };
    ws.onerror = () => { this.closed = true; this._wake(); };
  }

  /** Open a tunnel to ``onion`` through the entrance at ``base`` (an http(s) URL). */
  static open(base, onion, timeoutMs = 90000) {
    const u = new URL("onntp", base);
    u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
    u.searchParams.set("node", onion);
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(u);
      const c = new Connection(ws);
      const timer = setTimeout(() => { ws.close(); reject(new Error("the node did not answer in time")); }, timeoutMs);
      ws.onopen = async () => {
        try {
          const [code, text] = await c._response();
          clearTimeout(timer);
          if (code !== 200 && code !== 201) throw new NNTPError(code, text);
          resolve(c);
        } catch (err) { clearTimeout(timer); reject(err); }
      };
      ws.addEventListener("close", (e) => {
        clearTimeout(timer);
        reject(new Error(e.reason || "the entrance closed the connection"));
      });
    });
  }

  close() { try { this.ws.close(); } catch { /* already */ } }

  _wake() { const w = this.waiters; this.waiters = []; w.forEach((f) => f()); }

  async _readLine() {
    for (;;) {
      for (let i = 0; i + 1 < this.buf.length; i++) {
        if (this.buf[i] === 13 && this.buf[i + 1] === 10) {
          const line = this.buf.slice(0, i);
          this.buf = this.buf.slice(i + 2);
          return line;
        }
      }
      if (this.closed) throw new Error(this.closeReason || "connection closed");
      await new Promise((r) => this.waiters.push(r));
    }
  }

  async _response() {
    const line = fromUtf8(await this._readLine());
    const code = parseInt(line.slice(0, 3), 10);
    return [code, line.slice(4)];
  }

  /** A dot-terminated block, dot-unstuffed, as raw bytes with CRLF line ends. */
  async _block(limit = 1 << 20) {
    const lines = [];
    let size = 0;
    for (;;) {
      let line = await this._readLine();
      if (line.length === 1 && line[0] === 46) break;
      if (line[0] === 46) line = line.slice(1);
      size += line.length + 2;
      if (size > limit) throw new Error("block too large");
      lines.push(line, utf8("\r\n"));
    }
    return concat(...lines);
  }

  _send(text) { this.ws.send(typeof text === "string" ? utf8(text) : text); }

  _serial(fn) {
    const run = this.queue.then(fn, fn);
    this.queue = run.catch(() => {});
    return run;
  }

  command(line, expect = []) {
    return this._serial(async () => {
      this._send(line + "\r\n");
      const [code, text] = await this._response();
      if (expect.length && !expect.includes(code)) throw new NNTPError(code, text);
      return [code, text];
    });
  }

  multiline(line, expect) {
    return this._serial(async () => {
      this._send(line + "\r\n");
      const [code, text] = await this._response();
      if (code !== expect) throw new NNTPError(code, text);
      return fromUtf8(await this._block()).split("\r\n").filter((l, i, a) => i < a.length - 1 || l);
    });
  }

  capabilities() { return this.multiline("CAPABILITIES", 101); }

  async group(gid) {
    const [, text] = await this.command(`GROUP ${gid}`, [211]);
    const [n, lo, hi] = text.split(/\s+/).map(Number);
    return { count: n, low: lo, high: hi };
  }

  /** An article's raw bytes, or null if the node does not have it. */
  article(ident) {
    return this._serial(async () => {
      this._send(`ARTICLE ${ident}\r\n`);
      const [code] = await this._response();
      if (code !== 220) return null;
      return this._block(70000);
    });
  }

  /** XUN-MANIFESTS (UN-RFC 0041) for one group: the manifest's raw bytes, or null. */
  manifest(gid) {
    return this._serial(async () => {
      this._send(`XUN-MANIFESTS\r\n${gid}\r\n.\r\n`);
      let [code, text] = await this._response();
      if (code !== 296) throw new NNTPError(code, text);
      [code, text] = await this._response();
      if (code === 220) return this._block(70000);
      return null;
    });
  }

  post(raw) {
    return this._serial(async () => {
      this._send("POST\r\n");
      let [code, text] = await this._response();
      if (code !== 340) throw new NNTPError(code, text);
      // the article ends with CRLF already: no extra empty line before the terminator
      const body = fromUtf8(raw).replace(/\r\n$/, "");
      const lines = body.split("\r\n").map((l) => (l.startsWith(".") ? "." + l : l));
      this._send(utf8(lines.join("\r\n") + "\r\n.\r\n"));
      [code, text] = await this._response();
      if (code !== 240) throw new NNTPError(code, text);
      return text;
    });
  }
}
