"""The entrance as a mirror of Unseenet's releases (unseenet/unseenet#264).

Turned on with ENTRANCE_MIRROR=portable (the portable zips and the USB zip,
about 280 MB a version) or =all (also a stick's update and its image, about
1.1 GB). Releases are announced in ``unseenet.releases``: each post carries a
release's SHA256SUMS.txt and its signature, which is checked against the keys
clients trust (the project key; the release key for development builds). For
the newest release of each channel (ENTRANCE_MIRROR_CHANNELS, default
"stable dev") the files are fetched from GitHub or from another mirror, each
checked against the signed checksums, and served at /mirror/<channel>/<name> -
on the onion address too, which is how clients over Tor reach it. Only the
newest version of each channel is kept.

Clients use nothing from a mirror that does not match the signature, so a
mirror needs no trust: it can only withhold.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import shutil
from pathlib import Path

import aiohttp

log = logging.getLogger("unseenet-entrance")

REPO = "https://github.com/unseenet/unseenet"
SUMS, SIG = "SHA256SUMS.txt", "SHA256SUMS.txt.sig"
PORTABLE = {"linux-x86_64", "windows-x86_64", "macos-aarch64", "macos-x86_64", "usb"}
ALL = PORTABLE | {"x86_64-update", "img"}
_HEAD = re.compile(r"^Unseenet (?P<version>[0-9][0-9A-Za-z.+]*), (?P<channel>stable|dev)$")
_NAME = re.compile(r"^unseenet-(?P<version>[0-9][0-9A-Za-z.+]*?)-(?P<what>x86_64-update|usb|"
                   r"linux-x86_64|windows-x86_64|macos-aarch64|macos-x86_64)\.zip$"
                   r"|^unseenet-(?P<iversion>[0-9][0-9A-Za-z.+]*?)-x86_64\.img\.xz$")


def parse_post(body: str) -> tuple[str, bytes, str] | None:
    """(channel, sums, sig) from a post in unseenet.releases (unseenet.update.release_post)."""
    lines = body.replace("\r\n", "\n").split("\n")
    m = _HEAD.match(lines[0]) if lines else None
    if not m or f"{SUMS}:" not in lines or f"{SIG}:" not in lines:
        return None
    i, j = lines.index(f"{SUMS}:"), lines.index(f"{SIG}:")
    if j < i:
        return None
    sums = "\n".join(lines[i + 1:j]).strip("\n") + "\n"
    return m.group("channel"), sums.encode(), "\n".join(lines[j + 1:]).strip("\n") + "\n"


def kind(name: str) -> str | None:
    m = _NAME.match(name)
    if not m:
        return None
    return m.group("what") or "img"


def newest(bodies: list[str], channel: str) -> tuple[str, bytes, str] | None:
    """(version, sums, sig) of the newest release of ``channel`` that checks out."""
    from unseenet import update
    best = None
    for body in bodies:
        post = parse_post(body)
        if not post or post[0] != channel:
            continue
        _, sums, sig = post
        try:
            update.check_signature(sums, sig, channel)
        except update.UpdateError:
            continue
        versions = [m.group("version") for m in map(_NAME.match, update.parse_sums(sums)) if m and m.group("version")]
        if versions and (best is None or update.version_key(versions[0]) > update.version_key(best[0])):
            best = (versions[0], sums, sig)
    return best


def github_url(channel: str, version: str, name: str) -> str:
    tag = "dev" if channel == "dev" else f"v{version}"
    return f"{REPO}/releases/download/{tag}/{name}"


class Mirror:
    def __init__(self, cfg, releases, entrances):
        self.cfg, self.releases, self.entrances = cfg, releases, entrances
        self.wanted = ALL if cfg.mirror == "all" else PORTABLE
        self.failed: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        return self.cfg.mirror in ("portable", "all") and self.cfg.mirror_dir is not None

    def versions(self) -> dict[str, str]:
        """What this mirror has: {channel: version}."""
        out = {}
        if not self.enabled:
            return out
        for channel in self.cfg.mirror_channels:
            try:
                out[channel] = (self.cfg.mirror_dir / channel / "VERSION").read_text().strip()
            except OSError:
                pass
        return out

    def file(self, channel: str, name: str) -> Path | None:
        """A file this mirror serves, or None."""
        if not self.enabled or channel not in self.cfg.mirror_channels or "/" in name:
            return None
        d = self.cfg.mirror_dir / channel
        try:
            listed = {n for n in _sums_names((d / SUMS).read_bytes())} | {SUMS, SIG}
        except OSError:
            return None
        f = d / name
        return f if name in listed and f.is_file() else None

    async def check(self) -> None:
        from .release import signed_bodies
        try:
            bodies = signed_bodies(self.releases, self.cfg.release_owner or _project_key(),
                                   self.cfg.release_group)
        except Exception as exc:          # noqa: BLE001
            log.warning("mirror: the list of releases cannot be trusted: %s", exc)
            return
        for channel in self.cfg.mirror_channels:
            found = newest(bodies, channel)
            if not found or self.versions().get(channel) == found[0]:
                continue
            if self.failed.get(found[0], 0) >= 3:
                continue
            try:
                await self._fetch(channel, *found)
                print(f"mirror: {channel} is now Unseenet {found[0]}", flush=True)
            except Exception as exc:      # noqa: BLE001  next time
                self.failed[found[0]] = self.failed.get(found[0], 0) + 1
                log.warning("mirror: %s %s: %s", channel, found[0], exc)

    async def _others(self, session: aiohttp.ClientSession, channel: str, version: str) -> list[str]:
        """Other entrances that mirror this version, in random order."""
        from .release import entrance_urls
        try:
            urls = entrance_urls(self.entrances, self.cfg.release_owner or _project_key())
        except Exception:                 # noqa: BLE001
            return []
        out = []
        for base in random.sample(urls, len(urls)):
            try:
                async with session.get(base + ".well-known/unseenet-entrance",
                                       timeout=aiohttp.ClientTimeout(total=20)) as r:
                    info = json.loads(await r.text())
                if (info.get("mirror") or {}).get(channel) == version:
                    out.append(base)
            except Exception:             # noqa: BLE001
                continue
        return out

    async def _fetch(self, channel: str, version: str, sums: bytes, sig: str) -> None:
        from unseenet import update
        table = update.parse_sums(sums)
        names = sorted(n for n in table if kind(n) in self.wanted)
        root = self.cfg.mirror_dir
        part = root / f".{channel}.part"
        shutil.rmtree(part, ignore_errors=True)
        part.mkdir(parents=True)
        current = root / channel
        async with aiohttp.ClientSession(headers={"User-Agent": "unseenet-entrance"}) as s:
            others = None
            for name in names:
                old = current / name
                if old.is_file() and _sha256(old) == table[name]:
                    os.link(old, part / name) if _same_fs(old, part) else shutil.copy2(old, part / name)
                    continue
                if others is None:
                    others = await self._others(s, channel, version)
                sources = [github_url(channel, version, name)] + \
                    [b + f"mirror/{channel}/{name}" for b in others]
                for url in sources:
                    try:
                        await _download(s, url, part / name)
                    except Exception as exc:  # noqa: BLE001  the next source
                        log.warning("mirror: %s: %s", url, exc)
                        continue
                    if _sha256(part / name) == table[name]:
                        break
                    log.warning("mirror: %s does not match the signed checksum", url)
                else:
                    raise ValueError(f"{name} could not be fetched from any source")
        # only what is listed, and only what this mirror keeps
        (part / SUMS).write_bytes(sums)
        (part / SIG).write_text(sig)
        (part / "VERSION").write_text(version + "\n")
        old = root / f".{channel}.old"
        shutil.rmtree(old, ignore_errors=True)
        if current.exists():
            current.rename(old)
        part.rename(current)
        shutil.rmtree(old, ignore_errors=True)


def _sums_names(sums: bytes) -> list[str]:
    from unseenet import update
    return list(update.parse_sums(sums))


def _project_key() -> str:
    from .release import PROJECT_KEY
    return PROJECT_KEY


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _same_fs(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_dev == b.stat().st_dev
    except OSError:
        return False


async def _download(session: aiohttp.ClientSession, url: str, dest: Path) -> None:
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=3600, sock_read=120)) as r:
        if r.status != 200:
            raise ValueError(f"HTTP {r.status}")
        with open(dest, "wb") as f:
            async for chunk in r.content.iter_chunked(1 << 20):
                f.write(chunk)
