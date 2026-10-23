"""The graphical client's backend, running in the browser (UN-RFC 0043 section 6).

The real unseenet core with its local client API (UN-RFC 0046) and unseenet-gui's
own routes run in Pyodide inside a Web Worker. Only three things differ from the portable
client:

- the network: no Tor here; each connection is a WebSocket to the web entrance,
  which carries the bytes to the node over Tor (``webtransport``);
- threads: Pyodide has none, so work the core hands to a thread (key
  derivation, postage) runs inline - this is a worker, the page stays responsive;
  key-ripening segments, which take minutes to hours, are proved in a worker of
  their own instead (``_patch_ripening``, #27);
- the vault lives in the worker's memory; the page opens and saves it as a file.

Requests from the page are answered by the same aiohttp application the
portable client serves on 127.0.0.1, so the two cannot drift apart.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from pathlib import Path
from unittest import mock

VAULT = Path("/vault/unseenet.vault")
HOST = "127.0.0.1:0"               # what the GUI's guard expects (port 0 = not bound)

_app = None
_runtime = None
_token = secrets.token_urlsafe(32)
_post = None                         # postMessage to the page, set by start()


def _patch_threads() -> None:
    """Run what the core gives a thread inline: Pyodide cannot start threads."""
    loop = asyncio.get_event_loop()

    def run_in_executor(_executor, fn, *args):
        fut = loop.create_future()
        try:
            fut.set_result(fn(*args))
        except BaseException as exc:      # noqa: BLE001  handed to the awaiting code
            fut.set_exception(exc)
        return fut

    async def to_thread(fn, /, *args, **kwargs):
        return fn(*args, **kwargs)

    loop.run_in_executor = run_in_executor
    asyncio.to_thread = to_thread


def _patch_ripening(ripening_js) -> None:
    """Prove key-ripening segments (UN-RFC 0025) in the ripening worker that
    worker.js runs: ``ripening_js.prove(seed, x, bits, t)`` gives the result's
    bytes, or null when ``ripening_js.stop()`` stopped it. The core asks for
    this through ``Session._prove_segment`` (unseenet#323)."""
    from unseenet.client import session
    from unseenet.core import ripening as rp

    if not hasattr(session.Session, "_prove_segment"):     # a core before 0.4.2
        rp.available = lambda: False                           # no ripening, as before
        return
    from pyodide.ffi import to_js

    async def stop_when_gone(run_file) -> None:
        while Path(run_file).exists():
            await asyncio.sleep(0.5)
        ripening_js.stop()

    async def prove_segment(self, public_key, start, t, run_file=None):
        proving = ripening_js.prove(to_js(rp.seed(public_key)), to_js(bytes(start)),
                                    rp.DISC_BITS, t)
        watch = asyncio.ensure_future(stop_when_gone(run_file)) if run_file else None
        try:
            result = await proving
        finally:
            if watch:
                watch.cancel()
        if result is None:                     # stopped: started again later (section 3.2)
            return None
        data = result.to_bytes()
        return rp.Segment(t, data[:rp.FORM_SIZE], data[rp.FORM_SIZE:])

    session.Session._prove_segment = prove_segment


def _patch_network(entrance_url: str) -> None:
    import webtransport
    from unseenet.client import session

    webtransport.install(entrance_url)

    async def no_tor(self, status):       # the entrance runs Tor, not this page
        return 0

    session.Session._ensure_tor = no_tor


def _summary(data: dict) -> tuple[str, dict]:
    """The vault without its ripening chains, as a digest, and each key's level
    (computed, published). Cheap: the offline cache is a sidecar, not in ``data``."""
    from unseenet.core import ripening as rp

    rest = {k: v for k, v in data.items() if k != "ripening"}
    digest = hashlib.sha256(json.dumps(rest, sort_keys=True, default=str).encode()).hexdigest()
    levels = {}
    for key, entry in (data.get("ripening") or {}).items():
        t = sum(int(s.get("t", 0)) for s in entry.get("segments", []))
        lv, published = rp.level(t), rp.level(int(entry.get("published") or 0))
        if lv is not None or published is not None:      # a chain below level 0 is no level yet
            levels[key] = (lv, published)
    return digest, levels


def vault_change(data: dict, seen: dict) -> tuple[str, int | None]:
    """What changed since ``seen`` (the vault as opened or last saved), and the
    newest level: ``data`` (anything outside the ripening chains), ``level`` (a key
    reached a new level, or published one) or ``ripening`` (only a chain grew, a
    checkpoint minutes apart). The page offers to save on the first two (#32)."""
    digest, levels = _summary(data)
    if seen.get("digest") != digest:
        kind = "data"
    elif seen.get("levels") != levels:
        kind = "level"
    else:
        kind = "ripening"
    seen.update(digest=digest, levels=levels)
    top = max((lv for lv, _ in levels.values() if lv is not None), default=None)
    return kind, top


def _patch_vault() -> None:
    """Tell the page when the vault changed, and how, so it can offer to save it."""
    from unseenet.core import vault

    original_save = vault.Vault.save
    original_open = vault.Vault.open.__func__

    def save(self):
        original_save(self)
        if _post:
            seen = self.__dict__.setdefault("_web_seen", {})
            try:
                kind, level = vault_change(self.data, seen)
            except Exception:  # noqa: BLE001  the page then offers to save, as before
                kind, level = "data", None
            _post(json.dumps({"type": "vault-changed", "kind": kind, "level": level}))

    def open_(cls, *args, **kwargs):
        v = original_open(cls, *args, **kwargs)
        try:
            v._web_seen = dict(zip(("digest", "levels"), _summary(v.data)))
        except Exception:  # noqa: BLE001
            v._web_seen = {}
        return v

    vault.Vault.save = save
    vault.Vault.open = classmethod(open_)


def start(entrance_url: str, post_message, ripening_js=None) -> str:
    """Build the GUI's app. Returns the token the page sends with each request."""
    global _app, _runtime, _post
    _post = post_message
    _patch_threads()
    if ripening_js is not None:
        _patch_ripening(ripening_js)
    _patch_network(entrance_url)
    _patch_vault()
    from unseenet_gui.server import build

    VAULT.parent.mkdir(parents=True, exist_ok=True)
    _app, _runtime = build(VAULT, _token, page=False)      # the page comes from the shim
    _app.freeze()
    asyncio.get_event_loop().create_task(_pump_events())
    return _token


def switch_entrance(entrance_url: str) -> None:
    """Carry on through another entrance: the vault and everything open stay as they are."""
    import webtransport
    webtransport.switch(entrance_url)
    if _runtime is not None and _runtime.session is not None:
        _runtime.refresh()


async def _pump_events() -> None:
    """The GUI's event stream (its /api/events WebSocket), as messages to the page."""
    q = _runtime.listen()
    while True:
        event = await q.get()
        _post(json.dumps({"type": "event", "data": event}, ensure_ascii=False))


def _request(method: str, path: str, headers: dict, payload):
    """A web.Request as aiohttp's server would build it (Pyodide's aiohttp has
    no test_utils). Routing and middleware are then aiohttp's own."""
    from aiohttp.http import HttpVersion11, RawRequestMessage
    from aiohttp.web import Request
    from multidict import CIMultiDict, CIMultiDictProxy
    from yarl import URL

    hdrs = CIMultiDictProxy(CIMultiDict(headers))
    raw = tuple((k.encode(), v.encode()) for k, v in headers.items())
    message = RawRequestMessage(method, path, HttpVersion11, hdrs, raw, False, None,
                                False, False, URL(path))
    transport = mock.Mock()
    transport.get_extra_info.return_value = None
    protocol = mock.Mock(transport=transport, peername=None, sockname=None, ssl_context=None)
    writer = mock.Mock(transport=transport)
    return Request(message, payload, protocol, writer, mock.Mock(),
                   asyncio.get_event_loop(), client_max_size=256 * 1024)


async def handle(method: str, path: str, body: str | None) -> str:
    """Answer one API request with the GUI's own handlers and middleware."""
    from aiohttp import streams

    loop = asyncio.get_event_loop()
    payload = streams.StreamReader(mock.Mock(_reading_paused=False), 2 ** 16, loop=loop)
    data = (body or "").encode()
    if data:
        payload.feed_data(data)
    payload.feed_eof()
    headers = {"Host": HOST, "X-Unseenet-Token": _token}
    if data:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(data))
    resp = await _app._handle(_request(method, path, headers, payload))
    raw = resp.body if isinstance(resp.body, (bytes, bytearray)) else b""
    return json.dumps({"status": resp.status, "type": resp.content_type,
                       "body": bytes(raw).decode("utf-8", "replace")}, ensure_ascii=False)


def vault_in(data: bytes) -> None:
    """A vault file the person opened on the page, before unlocking."""
    if _runtime and _runtime.session is not None:
        raise RuntimeError("a vault is already open")
    VAULT.parent.mkdir(parents=True, exist_ok=True)
    VAULT.write_bytes(bytes(data))


def vault_out() -> bytes | None:
    return VAULT.read_bytes() if VAULT.exists() else None
