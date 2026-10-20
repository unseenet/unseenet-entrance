"""asyncio streams over a browser WebSocket to a web entrance (UN-RFC 0043).

Replaces unseenet.core.tor.open_connection when the core runs in Pyodide:
the entrance carries the bytes to the node over Tor, so ``Client`` works unchanged.
"""
import asyncio

from js import Uint8Array, WebSocket
from pyodide.ffi import create_proxy, to_js

ENTRANCE = None          # e.g. "wss://entrance.example/onntp"


class WSWriter:
    def __init__(self, ws):
        self.ws = ws
        self._closed = False

    def write(self, data):
        self.ws.send(to_js(bytes(data)))

    async def drain(self):
        return None

    def close(self):
        self._closed = True
        self.ws.close()

    def is_closing(self):
        return self._closed

    async def wait_closed(self):
        return None

    def get_extra_info(self, name, default=None):
        return default


async def open_connection(address, socks_port=None):
    host = address.rpartition(":")[0] or address
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=2 ** 20)
    ws = WebSocket.new(f"{ENTRANCE}?node={host}")
    ws.binaryType = "arraybuffer"
    opened = loop.create_future()

    def on_open(_e):
        if not opened.done():
            opened.set_result(True)

    def on_message(e):
        reader.feed_data(Uint8Array.new(e.data).to_bytes())

    def on_close(e):
        if not opened.done():
            opened.set_exception(ConnectionError(e.reason or "entrance closed the connection"))
        reader.feed_eof()

    for name, fn in (("open", on_open), ("message", on_message), ("close", on_close)):
        ws.addEventListener(name, create_proxy(fn))
    await opened
    return reader, WSWriter(ws)


def install(entrance_url):
    """Point the core's transport at an entrance."""
    global ENTRANCE
    ENTRANCE = entrance_url
    from unseenet.core import tor
    from unseenet import nntp
    tor.open_connection = open_connection
    nntp.tor.open_connection = open_connection
