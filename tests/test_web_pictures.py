"""The web client decodes pictures from the network in a worker of its own
(UN-RFC 0027 section 9.2): webbackend hands them to worker.js's picture worker,
since Pyodide cannot start the core's decoding process. The worker and Pyodide's
to_js are stood in for; worker.js itself is tried in a real browser by hand."""
import asyncio
import importlib
import sys
import types
from pathlib import Path

import pytest

from unseenet.client import decode

WEB_PY = Path(__file__).resolve().parent.parent / "web" / "py"


def png() -> bytes:
    """What the core's checks look at: the first octets (the worker does the rest)."""
    return b"\x89PNG\r\n\x1a\n" + bytes(32)


class Bytes:
    """A Uint8Array as Pyodide hands it to Python."""

    def __init__(self, data):
        self.data = data

    def to_bytes(self):
        return self.data


class Answer:
    """The worker's answer as a JS object seen from Python: only the fields it has."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


class Worker:
    def __init__(self, answer=None, wait=0.0):
        self.answer, self.wait, self.asked = answer, wait, []

    async def decode(self, octets, media_type, ms):
        self.asked.append((octets, media_type, ms))
        await asyncio.sleep(self.wait)
        return self.answer


@pytest.fixture(params=["elsewhere", "older core"])
def webbackend(request, monkeypatch):
    ffi = types.ModuleType("pyodide.ffi")
    ffi.to_js = lambda b: b
    monkeypatch.setitem(sys.modules, "pyodide", types.ModuleType("pyodide"))
    monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi)
    monkeypatch.syspath_prepend(str(WEB_PY))
    monkeypatch.setattr(decode, "picture_async", decode.picture_async)
    if request.param == "older core" or not hasattr(decode, "elsewhere"):
        monkeypatch.delattr(decode, "elsewhere", raising=False)
    else:
        monkeypatch.setattr(decode, "elsewhere", None)
    # no process may be started in a browser
    monkeypatch.setattr(decode.sys, "platform", "emscripten")

    def no_process(*a, **k):
        raise AssertionError("no process in a browser")
    monkeypatch.setattr(decode.asyncio, "create_subprocess_exec", no_process)
    return importlib.import_module("webbackend")


def test_a_picture_is_decoded_in_the_worker(webbackend):
    worker = Worker(Answer(bytes=Bytes(b"clean"), type="image/png", width=4, height=3))
    webbackend._patch_pictures(worker)
    got = asyncio.run(decode.picture_async(png(), "image/png"))
    assert (got.media_type, got.octets, got.width, got.height) == ("image/png", b"clean", 4, 3)
    assert worker.asked == [(png(), "image/png", int(decode.TIMEOUT * 1000))]


def test_the_core_checks_before_the_worker_sees_it(webbackend):
    worker = Worker(Answer(bytes=Bytes(b"x"), type="image/png", width=4, height=3))
    webbackend._patch_pictures(worker)
    with pytest.raises(decode.DecodeError, match="says it is image/jpeg"):
        asyncio.run(decode.picture_async(png(), "image/jpeg"))
    assert worker.asked == []


def test_the_workers_refusal_is_shown_as_text(webbackend):
    webbackend._patch_pictures(Worker(Answer(error="this picture could not be read")))
    with pytest.raises(decode.DecodeError, match="could not be read"):
        asyncio.run(decode.picture_async(png()))


def test_too_large_from_the_worker_is_refused(webbackend):
    worker = Worker(Answer(bytes=Bytes(b"x"), type="image/png", width=10_000, height=10_000))
    webbackend._patch_pictures(worker)
    with pytest.raises(decode.DecodeError, match="50 megapixels"):
        asyncio.run(decode.picture_async(png()))


def test_a_slow_worker_is_given_up(webbackend, monkeypatch):
    monkeypatch.setattr(decode, "TIMEOUT", 0.05)
    webbackend._patch_pictures(Worker(Answer(error=None), wait=5))
    with pytest.raises(decode.DecodeError, match="too long"):
        asyncio.run(decode.picture_async(png()))
