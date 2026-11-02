"""js/council.js counts a council's chain as unseenet's core does (UN-RFC 0054
section 5): the same copies, the same current manifest, the same forks. Runs node."""

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from unseenet.core import council as cn
from unseenet.core import crypto

HERE = Path(__file__).parent
DAY = timedelta(days=1)
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def js_count(cases: dict) -> dict:
    out = subprocess.run(["node", "--experimental-default-type=module", str(HERE / "council.test.mjs")],
                         input=json.dumps(cases), capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def py_count(gid: str, copies) -> dict:
    ch = cn.count_chain(gid, [(crypto.key_from_text(a), b) for a, b in copies])
    return {"seq": ch.current.seq if ch.current else None,
            "body": ch.current.body if ch.current else None, "disputed": ch.disputed}


def test_the_same_chain_as_the_core():
    ids = [crypto.Identity.generate() for _ in range(5)]
    keys = [i.key_text for i in ids]
    t0 = datetime(2026, 11, 2, tzinfo=timezone.utc)
    members = [cn.Member(i.public_key, t0, True) for i in ids[:3]]
    genesis = cn.council_body("x.lists", "channel", seq=1, date=t0, members=members,
                              fields={"lists": {"nodes": "x.nodes@abcdefghijklmnop"}, "title": "Lists æøå"})
    gid = cn.make_council_group_id("x.lists", genesis)
    g = cn.CouncilManifest.from_body(gid, genesis)
    # seq 2: one member leaves, two of three sign
    two = cn.next_body(g, date=t0 + 40 * DAY, members=members[:2])
    m2 = cn.CouncilManifest.from_body(gid, two)
    # seq 3: an admission on probation; seq 3': a different one - a fork when both count
    adm = cn.next_body(m2, date=t0 + 80 * DAY,
                       members=members[:2] + [cn.Member(ids[3].public_key, t0 + 80 * DAY, False)])
    adm2 = cn.next_body(m2, date=t0 + 80 * DAY,
                        members=members[:2] + [cn.Member(ids[4].public_key, t0 + 80 * DAY, False)])
    too_soon = cn.next_body(m2, date=t0 + 41 * DAY,
                            members=members[:2] + [cn.Member(ids[3].public_key, t0 + 41 * DAY, False)])
    cases = {
        "genesis only": [(k, genesis) for k in keys[:3]],
        "genesis not signed by all": [(k, genesis) for k in keys[:2]],
        "a stranger's copy": [(k, genesis) for k in keys[:3]] + [(keys[4], two)],
        "seq 2 by two of three": [(k, genesis) for k in keys[:3]] + [(k, two) for k in keys[:2]],
        "seq 2 by one": [(k, genesis) for k in keys[:3]] + [(keys[0], two)],
        "admission": [(k, genesis) for k in keys[:3]] + [(k, two) for k in keys[:2]]
                     + [(k, adm) for k in keys[:2]],
        "admission too soon": [(k, genesis) for k in keys[:3]] + [(k, two) for k in keys[:2]]
                              + [(k, too_soon) for k in keys[:2]],
        "fork": [(k, genesis) for k in keys[:3]] + [(k, two) for k in keys[:2]]
                + [(k, adm) for k in keys[:2]] + [(k, adm2) for k in keys[:2]],
    }
    got = js_count({"gid": gid, "cases": {n: [{"author": a, "body": b} for a, b in c]
                                          for n, c in cases.items()}})
    for name, copies in cases.items():
        assert got[name] == py_count(gid, copies), name
    assert got["admission"]["seq"] == 3 and got["fork"]["disputed"]
    assert got["genesis not signed by all"]["seq"] is None


def test_the_web_client_follows_the_list_to_its_council():
    """UN-RFC 0020 section 10: the project's group says it moved (``moved_to``); the
    entrance serves the council's copies and list beside it, and js/entrances.js reads
    the list its members posted - not a stranger's, and the old one until there is one."""
    import base64

    from unseenet.core import lists as ls
    from unseenet.core.article import new_article
    from unseenet.core.groups import canonical_json, make_manifest, manifest_article
    b64 = lambda a: base64.b64encode(a.to_bytes()).decode()       # noqa: E731
    project = crypto.Identity.generate()
    old = make_manifest(project, "x.entrances", "channel", pow_bits=0)
    gid_old = old.group

    def entrance_list(updated, url):
        return canonical_json({"v": 1, "updated": updated, "entrances": [{"url": url}]}) + "\n"
    old_post = new_article(gid_old, "list", entrance_list("2026-10-01", "https://old.example/")).finalize(project, 0)
    ids = [crypto.Identity.generate() for _ in range(3)]
    t0 = datetime(2026, 11, 2, tzinfo=timezone.utc)
    members = [cn.Member(i.public_key, t0, True) for i in ids]
    list_group = "x.entrances@abcdefghijklmnop"
    genesis = cn.council_body("x.lists", "channel", seq=1, date=t0, members=members,
                              fields={"lists": {"entrances": list_group}})
    council = cn.make_council_group_id("x.lists", genesis)
    copies = [b64(cn.council_article(i, council, genesis, pow_bits=0)) for i in ids]
    moved = manifest_article(project, gid_old, {**json.loads(old.body), "serial": 2,
                                                "moved_to": council}, pow_bits=0)
    stranger = crypto.Identity.generate()
    lists_ = [b64(new_article(list_group, "entrance list", entrance_list(u, url)).finalize(who, 0))
              for who, u, url in ((ids[0], "council 1 day 2026-11-03", "https://new.example/"),
                                  (stranger, "council 1 day 2026-11-09", "https://evil.example/"))]
    base = {"v": 1, "group": gid_old, "manifest": b64(moved), "posts": [b64(old_post)]}
    cases = {
        "council": {**base, "council": {"group": council, "list": list_group,
                                        "copies": copies, "posts": lists_}},
        "no list yet": {**base, "council": {"group": council, "list": list_group,
                                            "copies": copies, "posts": []}},
        "not every member signed": {**base, "council": {"group": council, "list": list_group,
                                                        "copies": copies[:2], "posts": lists_}},
        "not moved": {**base, "manifest": b64(old), "council": {
            "group": council, "list": list_group, "copies": copies, "posts": lists_}},
    }
    got = js_count({"bundles": cases, "owner": project.key_text, "group": gid_old})
    assert got["council"] == ["https://new.example/"]
    assert got["no list yet"] == got["not every member signed"] == ["https://old.example/"]
    assert got["not moved"] == ["https://old.example/"]
    assert ls.updated_day("council 1 day 2026-11-03")       # the form both read
