"""The browser's transport sends the copy of an identity through /copy (#53, UN-RFC 0043
4.5), with this session's tag; nodes still go through /onntp."""
import asyncio
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def wt(monkeypatch):
    opened = []
    js = types.ModuleType("js")
    js.Uint8Array = object
    js.WebSocket = types.SimpleNamespace(new=lambda url: opened.append(url))
    ffi = types.ModuleType("pyodide.ffi")
    ffi.create_proxy = ffi.to_js = lambda x: x
    monkeypatch.setitem(sys.modules, "js", js)
    monkeypatch.setitem(sys.modules, "pyodide", types.ModuleType("pyodide"))
    monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent.parent / "web" / "py"))
    sys.modules.pop("webtransport", None)
    import webtransport
    from unseenet.client.session import copying
    before = copying.CopyingMixin._copy_open
    yield webtransport, opened
    copying.CopyingMixin._copy_open = before
    from unseenet.core import tor
    import importlib
    importlib.reload(tor)


def test_copy_goes_through_slash_copy(wt, monkeypatch):
    webtransport, opened = wt
    webtransport.install("wss://entrance.example/onntp")
    urls = []

    async def fake_open(url):
        urls.append(url)
        return "r", "w"
    monkeypatch.setattr(webtransport, "_open", fake_open)
    from unseenet.client.session import copying
    onion = "a" * 56 + ".onion"
    got = asyncio.run(copying.CopyingMixin._copy_open(None, onion))
    assert got == ("r", "w")
    assert urls == [f"wss://entrance.example/copy?onion={onion}&session={webtransport.SESSION}"]
    asyncio.run(webtransport.open_connection("b" * 56 + ".onion:119"))
    assert urls[-1].startswith("wss://entrance.example/onntp?node=" + "b" * 56)
