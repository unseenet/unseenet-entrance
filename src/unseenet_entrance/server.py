"""A web entrance (UN-RFC 0043): a dumb tunnel from a browser's WebSocket to a
node's onion service, over Tor.

What this server does, and nothing more (UN-RFC 0043 sections 4-5):

- serves the web client's static files;
- for ``/onntp?node=<v3 onion>``, opens a connection over Tor to that onion
  address on port 119 and copies bytes both ways, unchanged;
- gives every WebSocket its own Tor circuit (SOCKS isolation with random
  credentials);
- answers ``/.well-known/unseenet-entrance``, ``/.well-known/unseenet-entrances.json``
  and ``/.well-known/unseenet-releases.json``;
- keeps the web client it serves up to date with signed versions (release.py),
  unless its operator turns that off.

It writes nothing about visitors anywhere: no access log, no error that names
an address. Limits are counted in memory, keyed by an HMAC of the address
under a key made at start and never written down.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import mimetypes
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
from .listfetch import PROJECT_GROUP, RELEASES_GROUP, ListCache, keep_fresh

mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("application/manifest+json", ".webmanifest")

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
    tls_cert: Path | None = None             # HTTPS on ``port`` when set (docs/certificates.md)
    tls_key: Path | None = None
    http_port: int = 0                       # plain HTTP: ACME challenges and a redirect to HTTPS
    acme_dir: Path | None = None             # where certbot --webroot puts its challenges
    onion_port: int = 0                      # plain HTTP on 127.0.0.1 for the onion service
    list_group: str = PROJECT_GROUP          # the signed list of entrances (UN-RFC 0043 8.1)
    release_group: str = RELEASES_GROUP      # signed versions of the web client (7.2)
    release_owner: str = ""                  # test only: the key that owns both (default: the project's)
    releases_dir: Path | None = None         # versions live here, web_dir is its "current" (7.3)
    auto_update: str = "web"                 # "web": the web client updates itself; "off"
    home: str = ""                           # "https://<ip>/": other names send pages here (one origin)
    mirror: str = ""                         # "portable" or "all": mirror Unseenet's releases (mirror.py)
    mirror_dir: Path | None = None
    mirror_channels: tuple[str, ...] = ("stable", "dev")
    info: dict = field(default_factory=dict)  # /.well-known/unseenet-entrance

    @classmethod
    def from_env(cls) -> "Config":
        """The configuration from ENTRANCE_* variables; ENTRANCE_MAX_TOTAL is "auto"
        unless set: what the machine's memory and open-file limit allow (#20)."""
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
        total = e("ENTRANCE_MAX_TOTAL", "auto").strip().lower()
        c.max_total = auto_max_total()[0] if total == "auto" else int(total)
        c.trust_forwarded = e("ENTRANCE_TRUST_FORWARDED", "") in ("1", "yes", "true")
        if e("ENTRANCE_TLS_CERT") and e("ENTRANCE_TLS_KEY"):
            c.tls_cert, c.tls_key = Path(e("ENTRANCE_TLS_CERT")), Path(e("ENTRANCE_TLS_KEY"))
        c.http_port = int(e("ENTRANCE_HTTP_PORT", c.http_port))
        c.onion_port = int(e("ENTRANCE_ONION_PORT", c.onion_port))
        c.list_group = e("ENTRANCE_LIST_GROUP", c.list_group)
        c.release_group = e("ENTRANCE_RELEASE_GROUP", c.release_group)
        c.release_owner = e("ENTRANCE_RELEASE_OWNER", c.release_owner)
        if e("ENTRANCE_RELEASES_DIR"):
            c.releases_dir = Path(e("ENTRANCE_RELEASES_DIR"))
        c.auto_update = e("ENTRANCE_AUTO_UPDATE", c.auto_update)
        c.home = e("ENTRANCE_HOME", c.home).strip().rstrip("/")
        c.mirror = e("ENTRANCE_MIRROR", c.mirror).strip().lower()
        if c.mirror in ("off", "no", "0"):
            c.mirror = ""
        if e("ENTRANCE_MIRROR_DIR"):
            c.mirror_dir = Path(e("ENTRANCE_MIRROR_DIR"))
        channels = tuple(x for x in e("ENTRANCE_MIRROR_CHANNELS", "").replace(",", " ").split()
                         if x in ("stable", "dev"))
        c.mirror_channels = channels or c.mirror_channels
        if e("ENTRANCE_ACME_DIR"):
            c.acme_dir = Path(e("ENTRANCE_ACME_DIR"))
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
    # 'wasm-unsafe-eval': the client's Python (Pyodide) is WebAssembly; no other eval
    # connect-src https: - to ask other entrances whether they answer (UN-RFC 0043 8.3)
    "Content-Security-Policy": ("default-src 'self'; connect-src 'self' https: http: ws: wss:; "
                                "img-src 'self' data: blob:; style-src 'self'; "
                                "script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; "
                                "object-src 'none'; base-uri 'none'; "
                                "frame-ancestors 'none'; form-action 'none'"),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


# The worker runs Python (Pyodide), which needs eval to load its compiled
# modules. A worker gets the policy of its own script, so only the worker - which
# has no access to the page - may eval; the page keeps the strict policy above.
WORKER_CSP = ("default-src 'self'; connect-src 'self' https: http: ws: wss:; "
              "script-src 'self' 'unsafe-eval' 'wasm-unsafe-eval'; object-src 'none'; base-uri 'none'")


@web.middleware
async def home(request: web.Request, handler):
    """With ENTRANCE_HOME, a name for the entrance only points to its one address: the
    app, its storage and the vault kept on the device belong to an origin, and the
    address is the one that keeps working when the name is blocked. The tunnel and
    what the entrance says about itself answer on every name."""
    target = request.app["cfg"].home
    if target and request.method in ("GET", "HEAD") and not request.path.startswith(("/onntp", "/.well-known/")):
        host = request.host.rsplit(":", 1)[0].strip("[]").lower()
        own = target.split("://", 1)[-1].split("/", 1)[0].strip("[]").lower()
        if host != own and not host.endswith(".onion") and host not in ("127.0.0.1", "localhost", "::1"):
            raise web.HTTPFound(target + request.path_qs)
    return await handler(request)


@web.middleware
async def headers(request: web.Request, handler):
    resp = await handler(request)
    if not isinstance(resp, web.WebSocketResponse):
        if request.path == "/worker.js":
            resp.headers["Content-Security-Policy"] = WORKER_CSP
        if request.path == "/unseenet-web.html":       # a file to keep and open from disk
            resp.headers["Content-Disposition"] = 'attachment; filename="unseenet-web.html"'
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)            # the jump page sets its own policy
        # anyone may read: the lists (8.7), and the files, for apps updating from here (7.2)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers.setdefault("Cache-Control", "no-cache")
    return resp


def web_version(cfg: Config) -> str:
    """The version of the web client served (version.json of the build)."""
    try:
        return str(json.loads((cfg.web_dir / "version.json").read_text()).get("version", ""))
    except (OSError, ValueError):
        return ""


async def entrance_info(request: web.Request) -> web.Response:
    cfg: Config = request.app["cfg"]
    mirror = request.app["mirror"].versions()
    return web.json_response({**cfg.info, "web": web_version(cfg), **({"mirror": mirror} if mirror else {})})


async def mirror_file(request: web.Request) -> web.StreamResponse:
    """A file of a release this entrance mirrors (unseenet/unseenet#264)."""
    f = request.app["mirror"].file(request.match_info["channel"], request.match_info["name"])
    if f is None:
        raise web.HTTPNotFound()
    return web.FileResponse(f, headers={"Content-Type": "application/octet-stream"})


async def releases_list(request: web.Request) -> web.Response:
    """Signed versions of the web client, as the network has them (UN-RFC 0043 7.2)."""
    return web.json_response(request.app["releases"].bundle())


async def entrances_list(request: web.Request) -> web.Response:
    """The signed list of entrances, as the network has it (UN-RFC 0043 8.2)."""
    return web.json_response(request.app["list"].bundle())


JUMP_TEMPLATE = "unseenet-entrances.template.html"
BUNDLE_MARK = '<script type="application/json" id="unseenet-bundle">null</script>'


async def jump_page(request: web.Request) -> web.Response:
    """The jump page with today's signed list in it - a file to keep (UN-RFC 0043 8.7)."""
    tpl = request.app["cfg"].web_dir / JUMP_TEMPLATE
    if not tpl.is_file():
        raise web.HTTPNotFound()
    text = tpl.read_text()
    m = re.search(r'<script type="module" id="jump">(.*?)</script>', text, re.S)
    if not m:
        raise web.HTTPNotFound()
    digest = base64.b64encode(hashlib.sha256(m.group(1).encode()).digest()).decode()
    bundle = json.dumps(request.app["list"].bundle()).replace("</", "<\\/")
    html = text.replace(BUNDLE_MARK, BUNDLE_MARK.replace("null", bundle))
    return web.Response(text=html, content_type="text/html", headers={
        "Content-Disposition": 'inline; filename="unseenet-entrances.html"',
        # its own script only; it asks other entrances whether they answer
        "Content-Security-Policy": (f"default-src 'none'; script-src 'sha256-{digest}'; "
                                    "style-src 'unsafe-inline'; connect-src https: http:; "
                                    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")})


async def nodes_json(request: web.Request) -> web.Response:
    cfg: Config = request.app["cfg"]
    return web.Response(body=cfg.nodes_file.read_bytes(), content_type="application/json")


async def static(request: web.Request) -> web.StreamResponse:
    root: Path = request.app["cfg"].web_dir.resolve()
    rel = request.match_info.get("path") or "index.html"
    path = (root / rel).resolve()
    if root not in path.parents or not path.is_file() or path.name.startswith("."):
        if rel == "index.html":
            return web.Response(status=503, text="The web client is not built: run tools/build_web.py\n")
        raise web.HTTPNotFound()
    return web.FileResponse(path)


def make_app(cfg: Config) -> web.Application:
    app = web.Application(middlewares=[headers, home])
    app["cfg"] = cfg
    app["limits"] = Limits(cfg.max_per_visitor, cfg.max_total)
    app["allowed"] = cfg.allowed_nodes()
    app["list"] = ListCache(cfg.list_group)
    app["releases"] = ListCache(cfg.release_group)
    from .mirror import Mirror
    app["mirror"] = Mirror(cfg, app["releases"], app["list"])
    app.router.add_get("/onntp", onntp)
    app.router.add_get("/.well-known/unseenet-entrance", entrance_info)
    app.router.add_get("/.well-known/unseenet-entrances.json", entrances_list)
    app.router.add_get("/.well-known/unseenet-releases.json", releases_list)
    app.router.add_get("/nodes.json", nodes_json)
    app.router.add_get("/mirror/{channel}/{name}", mirror_file)
    app.router.add_get("/unseenet-entrances.html", jump_page)
    app.router.add_get("/{path:.*}", static)
    return app


# --- HTTPS -----------------------------------------------------------------

def tls_context(cfg: Config):
    import ssl
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cfg.tls_cert, cfg.tls_key)
    return ctx


async def reload_tls(ctx, cfg: Config) -> None:
    """Pick up a renewed certificate without a restart (IP certificates last ~6 days)."""
    seen = cfg.tls_cert.stat().st_mtime
    while True:
        await asyncio.sleep(3600)
        try:
            mtime = cfg.tls_cert.stat().st_mtime
            if mtime != seen:
                ctx.load_cert_chain(cfg.tls_cert, cfg.tls_key)   # new handshakes use it
                seen = mtime
        except (OSError, ValueError) as exc:
            log.warning("could not reload the certificate: %s", exc)


def make_http_app(cfg: Config) -> web.Application:
    """Port 80: answers ACME http-01 challenges, sends everything else to HTTPS."""
    async def challenge(request: web.Request) -> web.StreamResponse:
        token = request.match_info["token"]
        if cfg.acme_dir is None or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
            raise web.HTTPNotFound()
        f = cfg.acme_dir / ".well-known" / "acme-challenge" / token
        if not f.is_file():
            raise web.HTTPNotFound()
        return web.Response(body=f.read_bytes(), content_type="text/plain")

    async def redirect(request: web.Request) -> web.Response:
        host = request.host.rsplit(":", 1)[0] if not request.host.startswith("[") else request.host.split("]")[0] + "]"
        port = "" if cfg.port == 443 else f":{cfg.port}"
        raise web.HTTPMovedPermanently(f"https://{host}{port}{request.path_qs}")

    app = web.Application()
    app.router.add_get("/.well-known/acme-challenge/{token}", challenge)
    app.router.add_route("*", "/{tail:.*}", redirect)
    return app


async def serve(cfg: Config) -> None:
    runners = []
    ctx = tls_context(cfg) if cfg.tls_cert else None
    app = make_app(cfg)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    nodes = sorted(app["allowed"]) if app["allowed"] else []
    if nodes:
        from .release import Updater
        updater, mirror = Updater(cfg, app["releases"], app["list"]), app["mirror"]

        async def on_fresh() -> None:
            if updater.enabled:
                await updater.check()
            if mirror.enabled:
                await mirror.check()
        asyncio.get_running_loop().create_task(
            keep_fresh([app["list"], app["releases"]], nodes, cfg.socks_host, cfg.socks_port,
                       on_fresh=on_fresh))
    await web.TCPSite(runner, cfg.host, cfg.port, ssl_context=ctx).start()
    runners.append(runner)
    if cfg.onion_port:      # Tor delivers onion visitors here; never on another interface
        await web.TCPSite(runner, "127.0.0.1", cfg.onion_port).start()
    if cfg.http_port:
        plain = web.AppRunner(make_http_app(cfg), access_log=None)
        await plain.setup()
        await web.TCPSite(plain, cfg.host, cfg.http_port).start()
        runners.append(plain)
    scheme = "https" if ctx else "http"
    print(f"unseenet-entrance {__version__} on {scheme}://{cfg.host}:{cfg.port}/"
          + (f" (and http on {cfg.http_port})" if cfg.http_port else "")
          + f", Tor SOCKS {cfg.socks_host}:{cfg.socks_port}, nodes: {cfg.nodes}"
          + f", at most {cfg.max_total} tunnels", flush=True)
    _, bound = auto_max_total()
    if bound == "open files" and os.environ.get("ENTRANCE_MAX_TOTAL", "auto") == "auto":
        print(f"note: the open-file limit ({_open_files()}) allows fewer tunnels than the "
              "memory would; raise it (LimitNOFILE=65536 in the systemd unit)", flush=True)
    try:
        if ctx:
            await reload_tls(ctx, cfg)
        else:
            await asyncio.Event().wait()
    finally:
        for r in runners:
            await r.cleanup()


TUNNEL_BYTES = 120 * 1024      # measured ~99 KB per tunnel with a 16 KiB TLS buffer, plus room
RESERVED_BYTES = 100 << 20     # the entrance itself, Python and aiohttp
FILES_PER_TUNNEL = 2           # the WebSocket and the SOCKS connection to Tor
RESERVED_FILES = 64            # listening sockets, logs, the release files


def _mem_available(path: str = "/proc/meminfo") -> int | None:
    try:
        for line in Path(path).read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _open_files() -> int | None:
    try:
        import resource
        soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (ImportError, ValueError, OSError):
        return None
    return None if soft == resource.RLIM_INFINITY else soft


def auto_max_total(mem: int | None = None, files: int | None = None) -> tuple[int, str]:
    """How many tunnels this machine carries (#20, UN-RFC 0043 4.2): the smaller of
    what its available memory and its open-file limit allow. Returns the number and
    which of the two decided it ("memory", "open files", or "default" when neither
    can be read)."""
    mem = _mem_available() if mem is None else mem
    files = _open_files() if files is None else files
    by_mem = (mem - RESERVED_BYTES) // TUNNEL_BYTES if mem else None
    by_files = (files - RESERVED_FILES) // FILES_PER_TUNNEL if files else None
    if by_mem is None and by_files is None:
        return Config.max_total, "default"
    if by_files is not None and (by_mem is None or by_files < by_mem):
        return max(10, by_files), "open files"
    return max(10, by_mem), "memory"


def main() -> None:
    # Warnings only, and none of them name a visitor (UN-RFC 0043 section 5).
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    logging.getLogger("aiohttp.access").disabled = True
    asyncio.run(serve(Config.from_env()))
