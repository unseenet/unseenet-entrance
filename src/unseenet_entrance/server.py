"""A web entrance (UN-RFC 0043): a dumb tunnel from a browser's WebSocket to a
node's onion service, over Tor.

What this server does, and nothing more (UN-RFC 0043 sections 4-5):

- serves the web client's static files;
- for ``/onntp?node=<v3 onion>``, opens a connection over Tor to that onion
  address on port 119 and copies bytes both ways, unchanged;
- gives every WebSocket its own Tor circuit (SOCKS isolation with random
  credentials);
- answers ``/.well-known/unseenet-entrance`` and ``/.well-known/unseenet-entrances.json``.

It writes nothing about visitors anywhere: no access log, no error that names
an address. Limits are counted in memory, keyed by an HMAC of the address
under a key made at start and never written down.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from aiohttp import WSMsgType, web
from python_socks import ProxyError
from python_socks.async_.asyncio import Proxy

from . import __version__

ONION_RE = re.compile(r"^[a-z2-7]{56}\.onion$")
ONNTP_PORT = 119
CHUNK = 65536
LIMIT_WINDOW = 3600           # seconds a counter is kept, at most (UN-RFC 0043 4.2)

log = logging.getLogger("unseenet-entrance")


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8043
    socks_host: str = "127.0.0.1"
    socks_port: int = 9050
    web_dir: Path = Path(__file__).resolve().parent.parent.parent / "web"
    nodes: str = "known"                     # "known" or "any" (UN-RFC 0043 4.1)
    nodes_file: Path = Path(__file__).resolve().parent / "nodes.json"
    extra_nodes: list[str] = field(default_factory=list)
    max_per_visitor: int = 6                 # open tunnels per visitor
    max_total: int = 300                     # open tunnels in all
    trust_forwarded: bool = False            # behind a local reverse proxy / tunnel
    info: dict = field(default_factory=dict)  # /.well-known/unseenet-entrance

    @classmethod
    def from_env(cls) -> "Config":
        c = cls()
        e = os.environ.get
        c.host = e("ENTRANCE_HOST", c.host)
        c.port = int(e("ENTRANCE_PORT", c.port))
        socks = e("ENTRANCE_SOCKS", f"{c.socks_host}:{c.socks_port}")
        c.socks_host, _, p = socks.rpartition(":")
        c.socks_port = int(p)
        if e("ENTRANCE_WEB_DIR"):
            c.web_dir = Path(e("ENTRANCE_WEB_DIR"))
        c.nodes = e("ENTRANCE_NODES", c.nodes)
        c.extra_nodes = [a for a in e("ENTRANCE_EXTRA_NODES", "").split() if a]
        c.max_per_visitor = int(e("ENTRANCE_MAX_PER_VISITOR", c.max_per_visitor))
        c.max_total = int(e("ENTRANCE_MAX_TOTAL", c.max_total))
        c.trust_forwarded = e("ENTRANCE_TRUST_FORWARDED", "") in ("1", "yes", "true")
        c.info = {
            "operator": e("ENTRANCE_OPERATOR", ""),
            "contact": e("ENTRANCE_CONTACT", ""),
            "onion": e("ENTRANCE_ONION", ""),
            "nodes": c.nodes,
            "statement": e("ENTRANCE_STATEMENT", "memory only, no logs, no third-party proxy"),
            "software": f"unseenet-entrance {__version__}",
        }
        return c

    def allowed_nodes(self) -> set[str] | None:
        """The onion addresses we tunnel to, or None for any v3 onion."""
        if self.nodes == "any":
            return None
        data = json.loads(self.nodes_file.read_text())
        found = {n["address"].split(":")[0] for n in data.get("nodes", [])}
        return found | set(self.extra_nodes)


class Limits:
    """Open tunnels per visitor and in total, in memory only."""

    def __init__(self, per_visitor: int, total: int):
        self.key = secrets.token_bytes(32)        # never written down
        self.per_visitor, self.total = per_visitor, total
        self.open: dict[bytes, tuple[int, float]] = {}
        self.count = 0

    def _tag(self, address: str) -> bytes:
        return hmac.new(self.key, address.encode(), hashlib.sha256).digest()[:16]

    def take(self, address: str) -> bytes | None:
        now = time.monotonic()
        for tag, (_, seen) in list(self.open.items()):
            if now - seen > LIMIT_WINDOW:
                del self.open[tag]
        if self.count >= self.total:
            return None
        tag = self._tag(address)
        n, _ = self.open.get(tag, (0, now))
        if n >= self.per_visitor:
            return None
        self.open[tag] = (n + 1, now)
        self.count += 1
        return tag

    def give(self, tag: bytes) -> None:
        self.count = max(0, self.count - 1)
        n, seen = self.open.get(tag, (1, 0.0))
        if n <= 1:
            self.open.pop(tag, None)
        else:
            self.open[tag] = (n - 1, seen)


def visitor_address(request: web.Request, cfg: Config) -> str:
    if cfg.trust_forwarded:
        for name in ("CF-Connecting-IP", "X-Forwarded-For"):
            value = request.headers.get(name)
            if value:
                return value.split(",")[0].strip()
    return request.remote or "?"


async def open_tunnel(cfg: Config, onion: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """A stream to ``onion:119`` on a Tor circuit of its own (SOCKS isolation)."""
    user, password = secrets.token_hex(8), secrets.token_hex(8)
    proxy = Proxy.from_url(f"socks5://{user}:{password}@{cfg.socks_host}:{cfg.socks_port}",
                           rdns=True)
    for pause in (2, 5, None):            # a fresh descriptor can take a moment
        try:
            sock = await proxy.connect(dest_host=onion, dest_port=ONNTP_PORT, timeout=60)
            return await asyncio.open_connection(sock=sock, limit=CHUNK * 2)
        except ProxyError:
            if pause is None:
                raise
            await asyncio.sleep(pause)
    raise ConnectionError("unreachable")


async def onntp(request: web.Request) -> web.StreamResponse:
    cfg: Config = request.app["cfg"]
    limits: Limits = request.app["limits"]
    onion = request.query.get("node", "").strip().lower()
    if not ONION_RE.match(onion):
        return web.Response(status=400, text="node must be a v3 onion address\n")
    allowed = request.app["allowed"]
    if allowed is not None and onion not in allowed:
        return web.Response(status=403, text="this entrance only connects to known nodes\n")
    tag = limits.take(visitor_address(request, cfg))
    if tag is None:
        return web.Response(status=429, text="too many connections\n")
    ws = web.WebSocketResponse(max_msg_size=CHUNK * 4, heartbeat=30)
    try:
        await ws.prepare(request)
        try:
            reader, writer = await open_tunnel(cfg, onion)
        except (ProxyError, OSError, asyncio.TimeoutError):
            await ws.close(code=4004, message=b"node unreachable over Tor")
            return ws

        async def up() -> None:            # browser -> node
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    writer.write(msg.data)
                elif msg.type == WSMsgType.TEXT:
                    writer.write(msg.data.encode("utf-8"))
                else:
                    break
                await writer.drain()

        async def down() -> None:          # node -> browser
            while True:
                data = await reader.read(CHUNK)
                if not data:
                    break
                await ws.send_bytes(data)

        tasks = [asyncio.create_task(up()), asyncio.create_task(down())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            writer.close()
            if not ws.closed:
                await ws.close()
        return ws
    finally:
        limits.give(tag)


SECURITY_HEADERS = {
    # nothing from elsewhere (UN-RFC 0043 6.4)
    "Content-Security-Policy": ("default-src 'self'; connect-src 'self' ws: wss:; "
                                "img-src 'self' data:; style-src 'self'; script-src 'self'; "
                                "worker-src 'self'; object-src 'none'; base-uri 'none'; "
                                "frame-ancestors 'none'; form-action 'none'"),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


@web.middleware
async def headers(request: web.Request, handler):
    resp = await handler(request)
    if not isinstance(resp, web.WebSocketResponse):
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        if request.path.startswith("/.well-known/"):
            resp.headers["Access-Control-Allow-Origin"] = "*"     # UN-RFC 0043 8.7
        resp.headers.setdefault("Cache-Control", "no-cache")
    return resp


async def entrance_info(request: web.Request) -> web.Response:
    return web.json_response(request.app["cfg"].info)


async def entrances_list(request: web.Request) -> web.Response:
    # The signed list (unseenet.entrances, UN-RFC 0043 8.1) does not exist yet;
    # until it does, an entrance lists only itself, unsigned.
    cfg: Config = request.app["cfg"]
    me = {**cfg.info, "url": f"{request.scheme}://{request.host}/"}
    return web.json_response({"v": 0, "signed": False, "entrances": [me]})


async def nodes_json(request: web.Request) -> web.Response:
    cfg: Config = request.app["cfg"]
    return web.Response(body=cfg.nodes_file.read_bytes(), content_type="application/json")


async def static(request: web.Request) -> web.StreamResponse:
    root: Path = request.app["cfg"].web_dir.resolve()
    rel = request.match_info.get("path") or "index.html"
    path = (root / rel).resolve()
    if root not in path.parents or not path.is_file() or path.name.startswith("."):
        raise web.HTTPNotFound()
    return web.FileResponse(path)


def make_app(cfg: Config) -> web.Application:
    app = web.Application(middlewares=[headers])
    app["cfg"] = cfg
    app["limits"] = Limits(cfg.max_per_visitor, cfg.max_total)
    app["allowed"] = cfg.allowed_nodes()
    app.router.add_get("/onntp", onntp)
    app.router.add_get("/.well-known/unseenet-entrance", entrance_info)
    app.router.add_get("/.well-known/unseenet-entrances.json", entrances_list)
    app.router.add_get("/nodes.json", nodes_json)
    app.router.add_get("/{path:.*}", static)
    return app


def main() -> None:
    # Warnings only, and none of them name a visitor (UN-RFC 0043 section 5).
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    logging.getLogger("aiohttp.access").disabled = True
    cfg = Config.from_env()
    print(f"unseenet-entrance {__version__} on http://{cfg.host}:{cfg.port}/ "
          f"(Tor SOCKS {cfg.socks_host}:{cfg.socks_port}, nodes: {cfg.nodes})", flush=True)
    web.run_app(make_app(cfg), host=cfg.host, port=cfg.port, access_log=None, print=None)
