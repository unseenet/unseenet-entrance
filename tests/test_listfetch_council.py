"""Once the list of entrances has moved to a list council (UN-RFC 0020 section 10),
the entrance serves the council's manifest copies and its list beside the old group,
for web clients to check (js/entrances.js): read here from a real node."""
import asyncio

import pytest
import json
from datetime import datetime, timezone

from unseenet.core import council as cn
from unseenet.core import crypto
from unseenet.core.article import new_article
from unseenet.core.groups import canonical_json, make_manifest, manifest_article
from unseenet.core.store import Store
from unseenet.node.server import Node, NodeConfig

from unseenet_entrance import listfetch


@pytest.mark.asyncio
async def test_the_councils_copies_and_list_are_fetched():
    ids = [crypto.Identity.generate() for _ in range(3)]
    t0 = datetime(2026, 11, 2, tzinfo=timezone.utc)
    node = Node(Store(), NodeConfig(min_pow=0, min_ripe=None, forward_delay=(0, 0)))
    owner = crypto.Identity.generate()
    lst = make_manifest(owner, "x.entrances", "channel", pow_bits=12)   # stands in for the list group
    genesis = cn.council_body("x.lists", "channel", seq=1, date=t0, min_level=0,
                              members=[cn.Member(i.public_key, t0, True) for i in ids],
                              fields={"lists": {"entrances": lst.group}, "min_pow": 0, "retention_days": 30})
    council = cn.make_council_group_id("x.lists", genesis)
    for i in ids:
        node.accept(cn.council_article(i, council, genesis, pow_bits=12).to_bytes())
    body = canonical_json({"v": 1, "updated": "council 1 day 2026-11-03",
                           "entrances": [{"url": "https://web.example/"}]}) + "\n"
    post = new_article(lst.group, "entrance list", body, from_name="Lists").finalize(ids[0], 12)
    node.accept(lst.to_bytes())
    node.accept(new_article(lst.group, "entrance list", body, from_name="Lists").finalize(owner, 12).to_bytes())
    lst2 = manifest_article(owner, lst.group, {**json.loads(lst.body), "serial": 2,
                                              "posters": [ids[0].key_text]}, pow_bits=12)
    node.accept(lst2.to_bytes())
    node.accept(post.to_bytes())
    server = await node.serve("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        assert (await listfetch._readline(reader)).startswith("20")
        got = await listfetch._council_list(reader, writer, council, "entrances")
    finally:
        writer.close()
        server.close()
    assert got["group"] == council and got["list"] == lst.group
    assert len(got["copies"]) == 3
    assert any(b"council 1 day 2026-11-03" in p for p in got["posts"])    # clients pick the members
    cache = listfetch.ListCache("x.entrances@old", follow="entrances")
    cache.manifest, cache.council = b"x", got
    b = cache.bundle()
    assert b["council"]["group"] == council and len(b["council"]["copies"]) == 3
    # where to look comes from the old group's manifest
    moved = manifest_article(owner, lst.group, {**json.loads(lst.body), "serial": 2,
                                                "moved_to": council}, pow_bits=12)
    assert listfetch.moved_to(moved.to_bytes()) == council
    assert listfetch.moved_to(lst.to_bytes()) is None
