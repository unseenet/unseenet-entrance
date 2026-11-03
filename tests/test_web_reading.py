"""The web client reads a group in fewer round trips (unseenet#916): with a core before
that fix, webbackend asks for the articles of a turn at once and fetches the group the
reader has open first. Against a fake node that answers only once it has every command,
so a client asking one at a time would wait for ever."""
import asyncio
import importlib
import sys
import types
from pathlib import Path

import pytest

from unseenet import nntp
from unseenet.client import runtime, session
from unseenet.client.localapi.api import groups

WEB_PY = Path(__file__).resolve().parent.parent / "web" / "py"


@pytest.fixture(params=["older core", "core with #916"])
def webbackend(request, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyodide", types.ModuleType("pyodide"))
    monkeypatch.syspath_prepend(str(WEB_PY))
    for cls, name in ((nntp.Client, "articles"), (session.Session, "_fetch_numbers"),
                      (session.Session, "reading_now"), (runtime.ClientRuntime, "_fetch"),
                      (groups.Groups, "_group_view")):
        if name in vars(cls):                    # put back after the test
            monkeypatch.setattr(cls, name, vars(cls)[name])
        else:                                    # and taken away again if added
            monkeypatch.setattr(cls, name, None, raising=False)
            monkeypatch.delattr(cls, name)
    if request.param == "older core":
        monkeypatch.delattr(nntp.Client, "articles", raising=False)
    elif not hasattr(nntp.Client, "articles"):
        pytest.skip("this core is older than #916")
    sys.modules.pop("webbackend", None)
    wb = importlib.import_module("webbackend")
    wb._patch_reading()
    return wb


def _article(n) -> bytes:
    return f"Message-ID: <{n}@x>\r\nSubject: {n}\r\n\r\nbody\r\n".encode()


async def _fake_node(answer, wait_for):
    async def handle(reader, writer):
        writer.write(b"200 fake\r\n")
        await writer.drain()
        lines = []
        while len(lines) < wait_for:
            line = await reader.readline()
            if not line:
                return
            lines.append(line.decode().strip())
        for i, line in enumerate(lines):
            out = answer(i, line)
            if out is None:
                writer.close()
                return
            writer.write(out)
        await writer.drain()
        await reader.read()
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    r, w = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
    c = nntp.Client(r, w)
    assert (await c._response())[0] == 200
    return server, c


def _ok(i, line):
    n = line.split()[1]
    return b"220 " + n.encode() + b" <x>\r\n" + _article(n) + b".\r\n"


def test_the_articles_of_a_turn_go_at_once(webbackend):
    asyncio.run(_test_the_articles_of_a_turn_go_at_once(webbackend))


async def _test_the_articles_of_a_turn_go_at_once(webbackend):
    server, c = await _fake_node(lambda i, line: b"430 no\r\n" if i == 1 else _ok(i, line), 4)
    got = await asyncio.wait_for(c.articles(["1", "2", "3", "4"]), 5)
    assert got[1] is None and all(g is not None for i, g in enumerate(got) if i != 1)
    c.writer.close()
    server.close()


def test_a_connection_broken_off_is_not_used_again(webbackend):
    asyncio.run(_test_a_connection_broken_off_is_not_used_again(webbackend))


async def _test_a_connection_broken_off_is_not_used_again(webbackend):
    server, c = await _fake_node(lambda i, line: None if i == 2 else _ok(i, line), 4)
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(c.articles(["1", "2", "3", "4"]), 5)
    assert c.writer.is_closing()
    server.close()


def test_a_broken_turn_keeps_the_turns_before(webbackend, monkeypatch):
    asyncio.run(_test_a_broken_turn_keeps_the_turns_before(webbackend, monkeypatch))


async def _test_a_broken_turn_keeps_the_turns_before(webbackend, monkeypatch):
    s = session.Session.__new__(session.Session)
    calls = []

    class C:
        async def group(self, gid):
            return 4, 1, 4

        async def articles(self, idents):
            return [_article(i) for i in idents]

    async def call(fn, node=None):
        calls.append(node)
        if len(calls) > 1:
            raise ConnectionError("gone")
        return await fn(C())
    monkeypatch.setattr(session.Session, "FETCH_BATCH", 2)
    s._call = call
    fetched, stopped = await s._fetch_numbers("g", [1, 2, 3, 4], "n")
    assert [n for n, _ in fetched] == [1, 2] and stopped == 3


class _Session:
    reading_now = None

    def __init__(self, order):
        self.order = order

    def is_slow(self, node):
        return False

    def node_for(self, gid):
        return "n"

    async def fetch_group(self, gid):
        self.order.append(gid)
        if gid == "a":                            # the reader opens "c" meanwhile
            self.reading_now = "c"


def test_the_open_group_comes_first(webbackend):
    asyncio.run(_test_the_open_group_comes_first(webbackend))


async def _test_the_open_group_comes_first(webbackend):
    order = []
    rt = runtime.ClientRuntime.__new__(runtime.ClientRuntime)
    rt.session = _Session(order)
    await rt._fetch(["a", "b", "c", "d"])
    assert order == ["a", "c", "b", "d"]


def test_opening_a_group_marks_it(webbackend, monkeypatch):
    class Api:
        session = _Session([])

        def _ready(self):
            return self.session

    with pytest.raises(Exception):                # the rest of the view needs a real session
        groups.Groups._group_view(Api(), "unseenet.help@x", False, None)
    assert Api.session.reading_now == "unseenet.help@x"
