"""A lab for updates of the web client (UN-RFC 0043 7.3): two entrances on this
machine, a test key that owns a list of entrances and a group of versions, and
signed posts - nothing goes to the network.

    python tests/lab_updates.py keys              the test key and groups (to build with)
    python tests/lab_updates.py serve LAB         serve LAB/a on :8051 and LAB/b on :8052

LAB/a and LAB/b are web trees (built, then copied); LAB/posts.json says which
versions each entrance announces: {"a": ["DIR", ...], "b": [...]}, where each DIR
holds a version's tree and its sums are posted as that version. Change the
files and send SIGHUP to read them again.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import signal
import sys
from pathlib import Path

from aiohttp import web
from unseenet.core import crypto
from unseenet.core.article import new_article
from unseenet.core.groups import make_manifest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from unseenet_entrance import server  # noqa: E402

OWNER = crypto.Identity.from_seed(hashlib.sha256(b"unseenet lab owner").digest())
POSTER = crypto.Identity.from_seed(hashlib.sha256(b"unseenet lab poster").digest())
LIST = f"lab.entrances@{OWNER.fingerprint}"
RELEASES = f"lab.releases@{OWNER.fingerprint}"
TOP = ["index.html", "lib.js", "shim.js", "worker.js", "entrance.css", "icon.svg", "sw.js",
       "version.json", "unseenet-entrances.template.html"]


def sums_of(tree: Path) -> str:
    """The sums the build would post: release.sums, recomputed from the files."""
    out = []
    for line in (tree / "release.sums").read_text().splitlines():
        _, _, path = line.partition("  ")
        out.append(f"{hashlib.sha256((tree / path).read_bytes()).hexdigest()}  {path}")
    return "\n".join(out) + "\n"


def post(group: str, subject: str, body: str) -> bytes:
    return new_article(group, subject, body).finalize(POSTER, 8).to_bytes()


def manifest(name: str) -> bytes:
    return make_manifest(OWNER, name, "channel", posters=[POSTER.key_text], view="data",
                         pow_bits=8).to_bytes()


def fill(app: web.Application, lab: Path, which: str) -> None:
    plan = json.loads((lab / "posts.json").read_text())
    entrances = {"v": 1, "updated": "2026-10-21", "entrances": [
        {"name": "lab-a", "url": "http://localhost:8051/"},
        {"name": "lab-b", "url": "http://localhost:8052/"}]}
    app["list"].manifest = manifest("lab.entrances")
    app["list"].posts = [post(LIST, "Entrances", json.dumps(entrances) + "\n")]
    app["releases"].manifest = manifest("lab.releases")
    rel = []
    for d in plan[which]:
        tree = lab / d
        v = json.loads((tree / "version.json").read_text())["version"]
        rel.append(post(RELEASES, f"Web client {v}", f"Unseenet web client {v}\n\nBuilt from lab.\n\n{sums_of(tree)}"))
    app["releases"].posts = rel[::-1]            # newest first, as the nodes give them


async def serve(lab: Path) -> None:
    apps = {}
    for which, port in (("a", 8051), ("b", 8052)):
        cfg = server.Config(host="127.0.0.1", port=port, web_dir=lab / which, nodes="any",
                            list_group=LIST, release_group=RELEASES)
        app = server.make_app(cfg)
        fill(app, lab, which)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        apps[which] = app
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGHUP, lambda: [fill(a, lab, w) for w, a in apps.items()])
    print("lab entrances on http://localhost:8051/ and http://localhost:8052/", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    if sys.argv[1:2] == ["keys"]:
        print(OWNER.key_text, LIST, RELEASES)
    elif sys.argv[1:2] == ["serve"]:
        asyncio.run(serve(Path(sys.argv[2])))
    else:
        print(__doc__)
