"""Fetch signed lists from the network: the list of entrances (UN-RFC 0043
section 8.2) and the versions of the web client (section 7.2).

The entrance serves them as ``/.well-known/unseenet-entrances.json`` and
``/.well-known/unseenet-releases.json``, so that web clients and the jump page
can learn about other entrances and newer versions. It keeps the
raw articles exactly as the network has them: the group's manifest (signed by
its owner) and its newest posts (signed by the owner or a poster). It checks
nothing itself - clients check every signature against the key they pin - so
an entrance can withhold entrances or versions but never add one. (Updating
its own web client, release.py checks them itself.)

Fetched over Tor from the known nodes, about once an hour.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import random
import secrets
import time

from python_socks.async_.asyncio import Proxy

log = logging.getLogger("unseenet-entrance")

PROJECT_GROUP = "unseenet.entrances@ph5fc5hop5232qsz"
RELEASES_GROUP = "unseenet.releases@ph5fc5hop5232qsz"
KEEP_POSTS = 5               # the newest few; clients take the newest valid one
SCAN = 20                    # articles looked at, newest first
SCAN_FILTERED = 400          # ... when only some subjects count: their headers only (HEAD)
WEB_RELEASE_SUBJECT = "Web client "   # tools/post_release.py: "Web client <version>"
REFRESH = 3600
MAX_COPIES = 400             # a council's manifest copies: 16 members, many manifests


class ListCache:
    def __init__(self, group: str, subject: str | None = None, follow: str | None = None):
        """``subject``: keep only posts whose Subject starts with it. unseenet.releases
        also carries the portable and bootable builds (unseenet#264), posted every
        night: without it the newest few were all builds, and the web client's own
        posts - days older - were never seen, so no entrance could install or
        update its web client."""
        self.group = group
        self.subject = subject
        # UN-RFC 0020 section 10: the kind of list to follow to a list council once the
        # group's manifest says it moved there (``moved_to``) - "entrances" for the list
        # of entrances. The council's copies and list are served beside the old group's,
        # raw as the network has them, for clients to check (js/entrances.js).
        self.follow = follow
        self.council: dict | None = None
        self.manifest: bytes | None = None
        self.posts: list[bytes] = []
        self.fetched: float = 0.0

    def bundle(self) -> dict:
        b64 = lambda raw: base64.b64encode(raw).decode()   # noqa: E731
        return {"v": 1, "group": self.group,
                "manifest": b64(self.manifest) if self.manifest else None,
                "posts": [b64(p) for p in self.posts],
                "fetched": int(self.fetched) or None,
                **({"council": {"group": self.council["group"], "list": self.council["list"],
                                "copies": [b64(p) for p in self.council["copies"]],
                                "posts": [b64(p) for p in self.council["posts"]]}}
                   if self.council else {})}


async def _readline(reader: asyncio.StreamReader) -> str:
    return (await asyncio.wait_for(reader.readline(), 60)).decode("utf-8", "replace").rstrip("\r\n")


async def _block(reader: asyncio.StreamReader, limit: int = 70000) -> bytes:
    lines, size = [], 0
    while True:
        line = await asyncio.wait_for(reader.readline(), 60)
        if line in (b".\r\n", b".\n", b""):
            break
        if line.startswith(b"."):
            line = line[1:]
        size += len(line)
        if size > limit:
            raise ValueError("block too large")
        lines.append(line.rstrip(b"\r\n"))
    return b"\r\n".join(lines) + b"\r\n"


def _subject(head: bytes) -> str:
    """The Subject of a block of headers ("" when it has none)."""
    for line in head.decode("utf-8", "replace").split("\r\n"):
        name, _, value = line.partition(":")
        if name.lower() == "subject":
            return value.strip()
    return ""


async def fetch_once(cache: ListCache, onion: str, socks_host: str, socks_port: int) -> bool:
    user, password = secrets.token_hex(8), secrets.token_hex(8)
    proxy = Proxy.from_url(f"socks5://{user}:{password}@{socks_host}:{socks_port}", rdns=True)
    sock = await proxy.connect(dest_host=onion, dest_port=119, timeout=90)
    reader, writer = await asyncio.open_connection(sock=sock, limit=1 << 20)
    try:
        if not (await _readline(reader)).startswith("20"):
            return False
        writer.write(f"XUN-MANIFESTS\r\n{cache.group}\r\n.\r\n".encode())
        await writer.drain()
        if not (await _readline(reader)).startswith("296"):
            return False
        line = await _readline(reader)
        manifest = await _block(reader) if line.startswith("220") else None
        writer.write(f"GROUP {cache.group}\r\n".encode())
        await writer.drain()
        line = await _readline(reader)
        posts: list[bytes] = []
        if line.startswith("211"):
            _, count, low, high = line.split()[:4]
            scan = SCAN_FILTERED if cache.subject else SCAN
            for n in range(int(high), max(int(low), int(high) - scan) - 1, -1):
                if cache.subject:
                    writer.write(f"HEAD {n}\r\n".encode())
                    await writer.drain()
                    if not (await _readline(reader)).startswith("221"):
                        continue
                    if not _subject(await _block(reader)).startswith(cache.subject):
                        continue
                writer.write(f"ARTICLE {n}\r\n".encode())
                await writer.drain()
                if (await _readline(reader)).startswith("220"):
                    posts.append(await _block(reader))
                if len(posts) >= KEEP_POSTS:
                    break
        if manifest is None:
            return False
        council = None
        moved = moved_to(manifest) if cache.follow else None
        if moved:
            council = await _council_list(reader, writer, moved, cache.follow)
            if council is None and cache.council and cache.council["group"] == moved:
                council = cache.council              # keep what we had; try again next time
        cache.manifest, cache.posts, cache.fetched = manifest, posts, time.time()
        cache.council = council
        return True
    finally:
        writer.close()


def moved_to(raw_manifest: bytes) -> str | None:
    """The ``moved_to`` of a raw manifest article (UN-RFC 0020 section 10), or None. Only
    where to look: clients check the manifest's signature themselves."""
    import json
    _, _, body = raw_manifest.partition(b"\r\n\r\n")
    try:
        g = json.loads(body.decode("utf-8")).get("moved_to")
    except (ValueError, AttributeError):
        return None
    return g if isinstance(g, str) and "@" in g and len(g) < 200 else None


async def _articles(reader, writer, numbers) -> list[bytes]:
    out = []
    for n in numbers:
        writer.write(f"ARTICLE {n}\r\n".encode())
        await writer.drain()
        if (await _readline(reader)).startswith("220"):
            out.append(await _block(reader))
    return out


async def _council_list(reader, writer, council: str, kind: str) -> dict | None:
    """A list council's manifest copies and the newest posts of its ``kind`` list, raw,
    or None when the council does not count or keeps no such list. The chain is counted
    here only to know which group to read; clients count it again."""
    from unseenet.core import council as cn
    from unseenet.core.article import Article
    from unseenet.nntp import strip_reader_headers
    writer.write(f"GROUP {council}\r\n".encode())
    await writer.drain()
    line = await _readline(reader)
    if not line.startswith("211"):
        return None
    _, _, low, high = line.split()[:4]
    writer.write(f"HDR Control {low}-{high}\r\n".encode())
    await writer.drain()
    if not (await _readline(reader)).startswith("225"):
        return None
    numbers = [ln.split()[0] for ln in (await _block(reader, 1 << 20)).decode("latin-1").split("\r\n")
               if len(ln.split()) == 2 and ln.split()[1].lower() == "manifest"][-MAX_COPIES:]
    copies, counted = [], []
    for raw in await _articles(reader, writer, numbers):
        try:
            art = Article.from_bytes(strip_reader_headers(raw))
            art.verify_signature()
        except Exception:  # noqa: BLE001 - not a copy that counts
            continue
        if art.group == council:
            copies.append(raw)
            counted.append((art.author_key, art.body))
    chain = cn.count_chain(council, counted)
    if chain.current is None or chain.disputed:
        return None
    group = (chain.current.data.get("lists") or {}).get(kind)
    if not isinstance(group, str):
        return None
    writer.write(f"GROUP {group}\r\n".encode())
    await writer.drain()
    line = await _readline(reader)
    posts: list[bytes] = []
    if line.startswith("211"):
        _, _, low, high = line.split()[:4]
        posts = await _articles(reader, writer, range(int(high), max(int(low), int(high) - KEEP_POSTS + 1) - 1, -1))
    return {"group": council, "list": group, "copies": copies, "posts": posts}


async def fetch_from_any(cache: ListCache, nodes: list[str], socks_host: str, socks_port: int) -> bool:
    for onion in random.sample(nodes, len(nodes)):
        try:
            if await fetch_once(cache, onion, socks_host, socks_port):
                return True
        except Exception as exc:              # noqa: BLE001  next node
            log.warning("%s: %s: %s", cache.group.partition("@")[0], onion[:12], type(exc).__name__)
    return False


async def keep_fresh(caches: list[ListCache], nodes: list[str], socks_host: str, socks_port: int,
                     on_fresh=None) -> None:
    """Fetch every list about once an hour; then ``on_fresh()`` (updating the web client)."""
    await asyncio.sleep(5)                       # let Tor settle after a restart
    while True:
        for cache in caches:
            await fetch_from_any(cache, nodes, socks_host, socks_port)
        if on_fresh is not None:
            try:
                await on_fresh()
            except Exception as exc:              # noqa: BLE001  try again next time
                log.warning("updating the web client: %s", exc)
        await asyncio.sleep(REFRESH if all(c.manifest for c in caches) else 300)
