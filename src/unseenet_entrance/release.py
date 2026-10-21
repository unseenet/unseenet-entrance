"""Keep the web client this entrance serves up to date (UN-RFC 0043 sections 7.2-7.3).

New versions are posted in ``unseenet.releases`` by a poster the project key
lists, each with the SHA-256 of every file. The entrance reads them through the
network (listfetch.py), and when there is a newer one than it serves it gets
the files - the release's zip from GitHub, or file by file from other listed
entrances that already have it - checks every one, and switches over without a
restart. Where the files come from does not matter: only matching files are
used. It never goes back to an older version, and never touches its own server
code: that is the operator's to update.

Versions are kept side by side in ENTRANCE_RELEASES_DIR; ``current`` is a link
to the one served (ENTRANCE_WEB_DIR). Turned off with ENTRANCE_AUTO_UPDATE=off.

    python -m unseenet_entrance.release --install     the newest signed version, now
    python -m unseenet_entrance.release --adopt DIR   a local build (web/), unsigned
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import aiohttp

log = logging.getLogger("unseenet-entrance")

PROJECT_KEY = "ed25519:det5dxevwbk36eds2indrzlazyri3fq3bgdzw6kx4ems3o267sya"
ZIP_URL = "https://github.com/unseenet/unseenet-entrance/releases/download/web-{v}/unseenet-web-{v}.zip"
VERSION_RE = re.compile(r"^\d{1,10}(\.\d{1,10}){0,5}$")
PATH_RE = re.compile(r"^(?!.*\.\.)(?!/)[A-Za-z0-9._\-/]{1,200}$")
NEEDED = ("index.html", "sw.js", "worker.js", "shim.js", "version.json")
MAX_ZIP = 96 << 20
MAX_FILE = 48 << 20
RETRY = 6 * 3600              # after a version could not be fetched


@dataclass
class Release:
    version: str
    files: dict[str, str]                # path -> sha256
    zip: tuple[str, str] | None          # (name, sha256)
    built: str = ""


def version_key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def parse_release(text: str) -> Release:
    """The body of a post in unseenet.releases (UN-RFC 0043 section 7.2)."""
    lines = text.splitlines()
    m = re.match(r"^Unseenet web client (\S+)\s*$", lines[0] if lines else "")
    if not m or not VERSION_RE.match(m.group(1)):
        raise ValueError("not a release of the web client")
    version, files, zipped, built = m.group(1), {}, None, ""
    for line in lines[1:]:
        h = re.match(r"^([0-9a-f]{64}) [ *]?(\S+)$", line)
        if not h:
            if line.startswith("Built from "):
                built = line[11:300]
            continue
        digest, path = h.groups()
        if not PATH_RE.match(path):
            raise ValueError(f"a file name is not allowed: {path[:60]}")
        if path == f"unseenet-web-{version}.zip":
            zipped = (path, digest)
        else:
            files[path] = digest
    missing = [n for n in NEEDED if n not in files]
    if missing:
        raise ValueError(f"the release has no {missing[0]}")
    return Release(version, files, zipped, built)


def signed_bodies(cache, owner: str, group: str) -> list[str]:
    """Bodies of the posts in ``cache`` (a ListCache) signed by the group's owner or a
    poster, after checking the manifest against the pinned owner."""
    from unseenet.core import crypto
    from unseenet.core.article import Article
    from unseenet.core.groups import Manifest
    if not cache.manifest:
        return []
    art = Article.from_bytes(cache.manifest)
    art.verify_signature()
    m = Manifest.from_article(art)
    if m.group_id != group or crypto.key_to_text(m.owner) != owner:
        raise ValueError(f"{group}: the manifest is not the pinned owner's")
    out = []
    for raw in cache.posts:
        try:
            post = Article.from_bytes(raw)
            post.verify_signature()
        except Exception:                 # noqa: BLE001  not a valid post: skip it
            continue
        if post.group == group and m.can_post_directly(post.author_key):
            out.append(post.body)
    return out


def valid_releases(cache, owner: str = PROJECT_KEY, group: str | None = None) -> list[Release]:
    """Every valid version in the cache, newest first."""
    found: dict[str, Release] = {}
    for body in signed_bodies(cache, owner, group or cache.group):
        try:
            r = parse_release(body)
        except ValueError:
            continue
        found.setdefault(r.version, r)
    return sorted(found.values(), key=lambda r: version_key(r.version), reverse=True)


def entrance_urls(cache, owner: str = PROJECT_KEY) -> list[str]:
    """The https addresses in the newest valid list of entrances."""
    best = None
    for body in signed_bodies(cache, owner, cache.group):
        try:
            data = json.loads(body)
            urls = [e["url"].rstrip("/") + "/" for e in data.get("entrances", [])
                    if isinstance(e, dict) and re.match(r"^https://[^/\s]+/?$", str(e.get("url", "")))]
        except (ValueError, TypeError, AttributeError):
            continue
        if urls and (best is None or str(data.get("updated", "")) > best[0]):
            best = (str(data.get("updated", "")), urls)
    return best[1] if best else []


def tree_version(path: Path) -> str | None:
    try:
        v = str(json.loads((path / "version.json").read_text()).get("version", ""))
        return v if VERSION_RE.match(v) else None
    except (OSError, ValueError):
        return None


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Updater:
    def __init__(self, cfg, releases, entrances):
        self.cfg, self.releases, self.entrances = cfg, releases, entrances
        self.owner = cfg.release_owner or PROJECT_KEY
        self.failed: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return self.cfg.auto_update == "web" and self.cfg.releases_dir is not None

    async def check(self) -> str | None:
        """Install the newest signed version if it is newer than the one served."""
        try:
            found = valid_releases(self.releases, self.owner, self.cfg.release_group)
        except Exception as exc:          # noqa: BLE001
            log.warning("the list of versions cannot be trusted: %s", exc)
            return None
        if not found:
            return None
        new, cur = found[0], tree_version(self.cfg.web_dir)
        if cur and version_key(new.version) <= version_key(cur):
            return None
        if self.failed.get(new.version, 0) > time.time():
            return None
        try:
            await install(self.cfg.releases_dir, new, self.cfg.web_dir, self.sources())
        except Exception as exc:          # noqa: BLE001
            self.failed[new.version] = time.time() + RETRY
            log.warning("could not update the web client to %s: %s", new.version, exc)
            return None
        print(f"web client updated to {new.version} (was {cur or 'none'})", flush=True)
        return new.version

    def sources(self) -> list[str]:
        try:
            return entrance_urls(self.entrances, self.owner)
        except Exception:                 # noqa: BLE001
            return []


async def _get(session: aiohttp.ClientSession, url: str, limit: int) -> bytes:
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=300)) as r:
        if r.status != 200:
            raise ValueError(f"{url}: HTTP {r.status}")
        data = await r.content.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f"{url}: too large")
        return data


async def fetch_files(rel: Release, have: Path | None, entrances: list[str]) -> dict[str, bytes]:
    """Every file of ``rel``, checked: unchanged ones from ``have``, the rest from the
    release's zip on GitHub, or from other entrances that serve this version."""
    out: dict[str, bytes] = {}
    if have is not None:
        for path, digest in rel.files.items():
            f = have / path
            if f.is_file():
                data = f.read_bytes()
                if sha256(data) == digest:
                    out[path] = data
    todo = [p for p in rel.files if p not in out]
    if not todo:
        return out
    async with aiohttp.ClientSession(headers={"User-Agent": "unseenet-entrance"}) as s:
        if rel.zip:
            try:
                raw = await _get(s, ZIP_URL.format(v=rel.version), MAX_ZIP)
                if sha256(raw) != rel.zip[1]:
                    raise ValueError("the zip does not match the signed checksum")
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    for path in todo:
                        data = z.read(path)
                        if sha256(data) == rel.files[path]:
                            out[path] = data
            except Exception as exc:      # noqa: BLE001  try the entrances
                log.warning("release zip of %s: %s", rel.version, exc)
        for base in entrances:
            todo = [p for p in rel.files if p not in out]
            if not todo:
                break
            try:
                info = json.loads(await _get(s, base + ".well-known/unseenet-entrance", 1 << 16))
                if info.get("web") != rel.version:
                    continue
                for path in todo:
                    data = await _get(s, base + path, MAX_FILE)
                    if sha256(data) != rel.files[path]:
                        raise ValueError(f"{path} does not match the signed checksum")
                    out[path] = data
            except Exception as exc:      # noqa: BLE001  the next entrance
                log.warning("files of %s from %s: %s", rel.version, base, exc)
    missing = [p for p in rel.files if p not in out]
    if missing:
        raise ValueError(f"{len(missing)} files could not be fetched, e.g. {missing[0]}")
    return out


def _write_tree(releases_dir: Path, name: str, files: dict[str, bytes]) -> Path:
    part = releases_dir / f".{name}.part"
    shutil.rmtree(part, ignore_errors=True)
    for path, data in files.items():
        f = part / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    final = releases_dir / name
    shutil.rmtree(final, ignore_errors=True)
    part.rename(final)
    return final


def switch(releases_dir: Path, name: str) -> None:
    """Point ``current`` at a version, at once (a new link renamed over the old one),
    and keep only that one and the one before."""
    link = releases_dir / "current"
    before = os.readlink(link) if link.is_symlink() else None
    tmp = releases_dir / ".current.new"
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(name)
    os.replace(tmp, link)
    for d in releases_dir.iterdir():
        if d.is_dir() and not d.is_symlink() and d.name not in (name, before) and not d.name.startswith("."):
            shutil.rmtree(d, ignore_errors=True)


async def install(releases_dir: Path, rel: Release, current: Path | None, entrances: list[str]) -> Path:
    releases_dir.mkdir(parents=True, exist_ok=True)
    have = current if current is not None and current.exists() else None
    files = await fetch_files(rel, have, entrances)
    # what the app compares with when it keeps this version (UN-RFC 0043 7.3)
    files["release.sums"] = "".join(f"{h}  {p}\n" for p, h in sorted(rel.files.items())).encode()
    tree = _write_tree(releases_dir, rel.version, files)
    switch(releases_dir, rel.version)
    return tree


def adopt(releases_dir: Path, web: Path) -> str:
    """Serve a local build (tools/build_web.py), unsigned: its files as release.sums lists them."""
    version = tree_version(web) or "0"
    files = {}
    for line in (web / "release.sums").read_text().splitlines():
        digest, _, path = line.partition("  ")
        data = (web / path).read_bytes()
        if sha256(data) != digest:
            raise ValueError(f"{path} changed since the build")
        files[path] = data
    files["release.sums"] = (web / "release.sums").read_bytes()
    releases_dir.mkdir(parents=True, exist_ok=True)
    _write_tree(releases_dir, version, files)
    switch(releases_dir, version)
    return version


async def install_newest(cfg, wait: int) -> str | None:
    """Fetch the lists over Tor and install the newest signed version."""
    from .listfetch import ListCache, fetch_from_any
    releases, entrances = ListCache(cfg.release_group), ListCache(cfg.list_group)
    nodes = sorted(cfg.allowed_nodes() or [])
    deadline = time.time() + wait
    while True:
        ok = await fetch_from_any(releases, nodes, cfg.socks_host, cfg.socks_port)
        if ok:
            await fetch_from_any(entrances, nodes, cfg.socks_host, cfg.socks_port)
            break
        if time.time() > deadline:
            return None
        await asyncio.sleep(10)
    u = Updater(cfg, releases, entrances)
    found = valid_releases(releases, u.owner, cfg.release_group)
    if not found:
        return None
    current = cfg.releases_dir / "current"
    if tree_version(current) == found[0].version:
        return found[0].version                   # already served
    await install(cfg.releases_dir, found[0], current if current.exists() else None, u.sources())
    return found[0].version


def main() -> None:
    from .server import Config
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--install", action="store_true", help="install the newest signed version")
    ap.add_argument("--adopt", type=Path, metavar="DIR", help="serve a local build instead (unsigned)")
    ap.add_argument("--wait", type=int, default=300, help="seconds to keep trying the nodes (--install)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    cfg = Config.from_env()
    if cfg.releases_dir is None:
        sys.exit("ENTRANCE_RELEASES_DIR is not set")
    if args.adopt:
        print(f"serving the local build {adopt(cfg.releases_dir, args.adopt)} (not signed)")
    elif args.install:
        v = asyncio.run(install_newest(cfg, args.wait))
        if v is None:
            print("no signed version of the web client found")
            sys.exit(2)
        print(f"installed the web client {v}")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
