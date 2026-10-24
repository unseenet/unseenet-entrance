"""One Tor circuit per session, and limits counted in sessions (UN-RFC 0043 4.1.1, 4.2, #21)."""
import secrets

from aiohttp.test_utils import make_mocked_request

from unseenet_entrance.server import Limits, session_tag

A, B = "198.51.100.7", "203.0.113.9"


def tag():
    return secrets.token_urlsafe(16)


def test_one_circuit_per_session_and_never_across():
    lim = Limits(per_visitor=64, total=1000)
    s1, s2 = tag(), tag()
    assert lim.credentials(A, s1) == lim.credentials(A, s1)      # one session: one circuit
    assert lim.credentials(A, s1) != lim.credentials(A, s2)      # another session
    assert lim.credentials(A, s1) != lim.credentials(B, s1)      # someone sending the same tag
    assert lim.credentials(A, None) != lim.credentials(A, None)  # no tag: a circuit of its own
    user, password = lim.credentials(A, s1)
    assert s1 not in user + password and len(user) == len(password) == 32
    assert Limits(64, 1000).credentials(A, s1) != lim.credentials(A, s1)   # a new key at start


def test_sessions_are_counted_not_tunnels():
    lim = Limits(per_visitor=64, total=1000, sessions=3)
    family = [tag() for _ in range(3)]           # three people behind one address
    tickets = [lim.take(A, s) for s in family for _ in range(4)]   # 4 tunnels each
    assert all(tickets) and lim.count == 12
    assert lim.take(A, tag()) is None            # a fourth session is refused
    fifth = lim.take(A, family[0])               # an open session gets more tunnels
    assert fifth
    assert lim.take(A, None) is None             # a tunnel without a tag is a session too
    assert lim.take(B, tag())                    # another visitor is not affected
    for t in tickets[:4] + [fifth]:              # the first person leaves
        lim.give(t)
    assert lim.take(A, tag())                    # room for a new session


def test_the_tunnel_cap_and_the_total_still_hold():
    lim = Limits(per_visitor=5, total=7, sessions=16)
    s = tag()
    got = [lim.take(A, s) for _ in range(5)]
    assert all(got) and lim.take(A, s) is None   # one session cannot open hundreds
    assert lim.take(B, tag()) and lim.take(B, tag())
    assert lim.take("192.0.2.1", tag()) is None  # 7 in all
    for t in got:
        lim.give(t)
    assert lim.count == 2 and len(lim.open) == 1


def test_only_well_formed_tags():
    def req(q):
        return make_mocked_request("GET", "/onntp?node=x" + q)
    good = tag()
    assert session_tag(req(f"&session={good}")) == good
    for bad in ("", "&session=short", "&session=" + "a" * 65, "&session=has%20space",
                "&session=a/b" + "c" * 30):
        assert session_tag(req(bad)) is None


def test_onion_visitors_are_told_apart_by_session():
    """Through the onion service everyone comes from 127.0.0.1: one session, one visitor."""
    from unseenet_entrance.server import Config, visitor_key
    cfg = Config(onion_port=8044, port=8043)
    s1, s2 = tag(), tag()
    assert visitor_key("127.0.0.1", 8044, cfg, s1) != visitor_key("127.0.0.1", 8044, cfg, s2)
    assert visitor_key("127.0.0.1", 8044, cfg, None) == visitor_key("127.0.0.1", 8044, cfg, None)
    assert visitor_key(A, 8043, cfg, s1) == A                   # the open web: the address
    assert visitor_key("127.0.0.1", 8043, Config(onion_port=0), s1) == "127.0.0.1"
    lim = Limits(per_visitor=64, total=1000, sessions=2)
    onion_people = [visitor_key("127.0.0.1", 8044, cfg, tag()) for _ in range(5)]
    assert all(lim.take(v, None) for v in onion_people)     # not 2 sessions for all of Tor
