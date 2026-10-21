"""The entrance as a mirror of Unseenet's releases (unseenet/unseenet#264)."""
import asyncio
import hashlib

import pytest
from aiohttp import web
from unseenet import update
from unseenet.core.article import new_article
from unseenet.core.crypto import Identity

import lab_updates as lab
from unseenet_entrance import mirror, server
from unseenet_entrance.listfetch import ListCache

KEY = Identity.from_seed(bytes(range(32)))


def release(files: dict[str, bytes], channel="dev"):
    sums = "".join(f"{hashlib.sha256(d).hexdigest()}  {n}\n" for n, d in sorted(files.items())).encode()
    sig = update.signature_text(sums, KEY)
    _, body = update.release_post(sums, sig, channel)
    return body


def test_mirror_fetches_checks_serves(tmp_path, monkeypatch):
    monkeypatch.setitem(update.TRUSTED, "dev", (KEY.key_text,))
    files = {"unseenet-9.1.dev1-linux-x86_64.zip": b"linux" * 1000,
             "unseenet-9.1.dev1-usb.zip": b"usb" * 1000,
             "unseenet-9.1.dev1-x86_64-update.zip": b"update"}          # not in "portable"
    served = dict(files)

    async def run():
        async def get(request):
            return web.Response(body=served[request.match_info["name"]])
        gh = web.Application()
        gh.router.add_get("/{name}", get)
        runner = web.AppRunner(gh)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 8061).start()
        monkeypatch.setattr(mirror, "github_url", lambda c, v, n: f"http://127.0.0.1:8061/{n}")
        rel = ListCache(lab.RELEASES)
        rel.manifest = lab.manifest("lab.releases")
        rel.posts = [new_article(lab.RELEASES, "r", release(files)).finalize(lab.POSTER, 8).to_bytes()]
        cfg = server.Config(release_group=lab.RELEASES, release_owner=lab.OWNER.key_text,
                            mirror="portable", mirror_dir=tmp_path, mirror_channels=("dev",))
        m = mirror.Mirror(cfg, rel, ListCache(lab.LIST))
        served["unseenet-9.1.dev1-usb.zip"] = b"changed"       # a source that lies
        await m.check()
        assert m.versions() == {}                               # nothing half-done is served
        served["unseenet-9.1.dev1-usb.zip"] = files["unseenet-9.1.dev1-usb.zip"]
        m.failed.clear()
        await m.check()
        await runner.cleanup()
        return m

    m = asyncio.run(run())
    assert m.versions() == {"dev": "9.1.dev1"}
    assert m.file("dev", "unseenet-9.1.dev1-usb.zip").read_bytes() == files["unseenet-9.1.dev1-usb.zip"]
    assert m.file("dev", "unseenet-9.1.dev1-x86_64-update.zip") is None     # portable only
    assert m.file("dev", update.SUMS) is not None and m.file("dev", "../etc") is None


def test_posts_that_do_not_check_out_are_ignored(monkeypatch):
    monkeypatch.setitem(update.TRUSTED, "dev", (KEY.key_text,))
    good = release({"unseenet-9.1.dev1-linux-x86_64.zip": b"x"})
    bad = good.replace("9.1.dev1", "9.2.dev1")                 # the signature no longer fits
    assert mirror.newest([bad, good], "dev")[0] == "9.1.dev1"
    assert mirror.newest([good], "stable") is None
    with pytest.raises(Exception):
        update.check_signature(*mirror.parse_post(bad)[1:], "dev")
