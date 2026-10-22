"""The graphical client's backend, running in the browser (UN-RFC 0043 section 6).

The real unseenet core with its local client API (UN-RFC 0046) and unseenet-gui's
own routes run in Pyodide inside a Web Worker. Only three things differ from the portable
client:

- the network: no Tor here; each connection is a WebSocket to the web entrance,
  which carries the bytes to the node over Tor (``webtransport``);
- threads: Pyodide has none, so work the core hands to a thread (key
  derivation, postage) runs inline - this is a worker, the page stays responsive;
- the vault lives in the worker's memory; the page opens and saves it as a file.

Requests from the page are answered by the same aiohttp application the
portable client serves on 127.0.0.1, so the two cannot drift apart.
"""
from __future__ import annotations

import asyncio
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


def _patch_network(entrance_url: str) -> None:
    import webtransport
    from unseenet.client import session

    webtransport.install(entrance_url)

    async def no_tor(self, status):       # the entrance runs Tor, not this page
        return 0

    session.Session._ensure_tor = no_tor


def _patch_vault() -> None:
    """Tell the page when the vault changed, so it can offer to save it."""
    from unseenet.core import vault

    original = vault.Vault.save

    def save(self):
        original(self)
        if _post:
            _post(json.dumps({"type": "vault-changed"}))

    vault.Vault.save = save


def start(entrance_url: str, post_message) -> str:
    """Build the GUI's app. Returns the token the page sends with each request."""
    global _app, _runtime, _post
    _post = post_message
    _patch_threads()
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
