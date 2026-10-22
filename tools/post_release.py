#!/usr/bin/env python3
"""Post a version of the web client in unseenet.releases, over Tor (used by CI).

    UNSEENET_ANNOUNCE_SEED=<hex seed> python tools/post_release.py out/unseenet-web-<v>.txt

The text is what tools/build_web.py --release-out wrote: the version and the
SHA-256 of every file (UN-RFC 0043 section 7.2). The key is the GitHub poster,
which the project key lists as a poster in the channel; apps and entrances
check every file against this post before they use it. Posted to every known
node that answers.

Key ripening (UN-RFC 0025): the posting goes through Session, so the header
and postage are the core's. CI never ripens: the key is ripened and its chain
published once, anywhere (``unseenet ripen --key-file ... --to 0 --publish``),
and the session takes that chain over from the key's ripe@ group. Without
one, this stops at once and says so.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from unseenet.core import crypto  # noqa: E402
from unseenet.core.poster import Poster  # noqa: E402
from unseenet_entrance.listfetch import RELEASES_GROUP  # noqa: E402
from unseenet_entrance.release import parse_release  # noqa: E402



async def post(text: str, key: crypto.Identity, dry_run: bool) -> int:
    rel = parse_release(text)                     # never post something that does not parse
    nodes = json.loads((ROOT / "src" / "unseenet_entrance" / "nodes.json").read_text())["nodes"]
    poster = Poster(key, [n["address"] for n in nodes], from_name="Unseenet project", tor="own",
                    log=lambda msg: print("  " + msg, flush=True))
    art = poster.build(RELEASES_GROUP, f"Web client {rel.version}", text)
    print(f"Posting web client {rel.version} ({len(rel.files)} files) as {key.key_text}", flush=True)
    if dry_run:                                   # the text as signed; postage is the nodes'
        print(art.finalize(key, 0).to_bytes().decode())
        return 1
    async with poster:
        if await poster.ripening_level() is None:
            sys.exit(f"{key.key_text} has no published ripening chain (UN-RFC 0025): ripen it "
                     "once outside CI - unseenet ripen --key-file KEY --to 0 --node NODE --publish")
        result = await poster.post_article(art)
    return len(result.accepted)


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = len(args) != len(sys.argv) - 1
    if len(args) != 1:
        sys.exit(__doc__.strip().splitlines()[2].strip())
    seed = os.environ.get("UNSEENET_ANNOUNCE_SEED", "").strip()
    if not seed and not dry:
        sys.exit("UNSEENET_ANNOUNCE_SEED is not set")
    key = crypto.Identity.from_seed(bytes.fromhex(seed)) if seed else crypto.Identity.generate()
    if asyncio.run(post(Path(args[0]).read_text(), key, dry)) == 0:
        sys.exit("no node took the post")


if __name__ == "__main__":
    main()
