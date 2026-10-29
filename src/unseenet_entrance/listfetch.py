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


class ListCache:
    def __init__(self, group: str, subject: str | None = None):
        """``subject``: keep only posts whose Subject starts with it. unseenet.releases
        also carries the portable and bootable builds (unseenet#264), posted every
        night: without it the newest few were all builds, and the web client's own
        posts - days older - were never seen, so no entrance could install or
        update its web client."""
        self.group = group
        self.subject = subject
        self.manifest: bytes | None = None
        self.posts: list[bytes] = []
        self.fetched: float = 0.0

    def bundle(self) -> dict:
        b64 = lambda raw: base64.b64encode(raw).decode()   # noqa: E731
        return {"v": 1, "group": self.group,
                "manifest": b64(self.manifest) if self.manifest else None,
                "posts": [b64(p) for p in self.posts],
                "fetched": int(self.fetched) or None}


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
        cache.manifest, cache.posts, cache.fetched = manifest, posts, time.time()
        return True
    finally:
        writer.close()


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
