"""release.py: which posts count, and an entrance updating its web client (UN-RFC 0043 7.3).

    python -m pytest tests/test_release.py          (the update test needs the lab:
                                                     tests/lab_updates.py serve LAB)
"""
import asyncio
import hashlib
import json
import os
import shutil
import socket
from pathlib import Path

import pytest
from unseenet.core import crypto
from unseenet.core.article import new_article

import lab_updates as lab
from unseenet_entrance import release
from unseenet_entrance.listfetch import ListCache

H = "0" * 64


def body(version="20261021.1000", extra=""):
    files = "".join(f"{H}  {n}\n" for n in release.NEEDED)
    return f"Unseenet web client {version}\n\nBuilt from test.\n\n{files}{extra}"


def cache_with(*bodies, poster=lab.POSTER):
    c = ListCache(lab.RELEASES)
    c.manifest = lab.manifest("lab.releases")
    c.posts = [new_article(lab.RELEASES, "v", b).finalize(poster, 8).to_bytes() for b in bodies]
    return c


def test_parse_and_order():
    r = release.parse_release(body(extra=f"{H}  unseenet-web-20261021.1000.zip\n"))
    assert r.version == "20261021.1000" and r.zip[0].endswith(".zip") and "index.html" in r.files
    found = release.valid_releases(cache_with(body("20261021.900"), body("20261021.1000")), lab.OWNER.key_text)
    assert [x.version for x in found] == ["20261021.1000", "20261021.900"]   # by number, not text


def test_as_a_node_serves_it():
    """Nodes add Path and Xref for newsreaders; they are not part of the signed article."""
    c = cache_with(body())
    add = lambda raw: raw.replace(b"\r\n\r\n", b"\r\nPath: not-for-mail\r\nXref: unseenet x:1\r\n\r\n", 1)  # noqa: E731
    c.manifest, c.posts = add(c.manifest), [add(p) for p in c.posts]
    assert [r.version for r in release.valid_releases(c, lab.OWNER.key_text)] == ["20261021.1000"]


def test_refused():
    for bad in ("Unseenet web client 1.x\n", body(extra=f"{H}  ../etc/passwd\n"),
                body().replace("worker.js", "other.js")):
        with pytest.raises(ValueError):
            release.parse_release(bad)
    stranger = crypto.Identity.from_seed(hashlib.sha256(b"not a poster").digest())
    assert release.valid_releases(cache_with(body(), poster=stranger), lab.OWNER.key_text) == []
    with pytest.raises(ValueError):                       # another owner's group
        release.valid_releases(cache_with(body()), lab.POSTER.key_text)


def _lab_up() -> bool:
    try:
        socket.create_connection(("127.0.0.1", 8052), 1).close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _lab_up(), reason="the lab is not running")
def test_update_from_another_entrance(tmp_path):
    labdir = Path(os.environ.get("LAB", Path.home() / "lab-upd"))
    c = ListCache(lab.RELEASES)
    c.manifest = lab.manifest("lab.releases")
    tree = labdir / "v2"
    v = json.loads((tree / "version.json").read_text())["version"]
    c.posts = [lab.post(lab.RELEASES, "v", f"Unseenet web client {v}\n\n{lab.sums_of(tree)}")]
    rel = release.valid_releases(c, lab.OWNER.key_text)[0]
    releases = tmp_path / "web"
    releases.mkdir()
    shutil.copytree(labdir / "v1", releases / "20261021.1000")
    release.switch(releases, "20261021.1000")
    asyncio.run(release.install(releases, rel, releases / "current", ["http://localhost:8052/"]))
    assert release.tree_version(releases / "current") == v
    assert os.readlink(releases / "current") == v
    sums = (releases / "current" / "release.sums").read_text()
    assert "shim.js" in sums and "index.html" in sums


def test_long_response_headers_are_read():
    """GitHub's downloads send a Content-Security-Policy longer than aiohttp's 8190."""
    import asyncio

    import aiohttp

    from unseenet_entrance.release import CLIENT_LIMITS, _get

    async def run():
        async def handle(r, w):
            await r.readuntil(b"\r\n\r\n")
            w.write(b"HTTP/1.1 200 OK\r\nContent-Security-Policy: " + b"x" * 20000
                    + b"\r\nContent-Length: 2\r\n\r\nok")
            await w.drain()
            w.close()
        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            async with aiohttp.ClientSession(**CLIENT_LIMITS) as s:
                return await _get(s, f"http://127.0.0.1:{port}/x.zip", 100)
        finally:
            server.close()
    assert asyncio.run(run()) == b"ok"
