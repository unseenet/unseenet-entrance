"""The web client proves key-ripening segments in a worker of its own (#27):
webbackend's override of Session._prove_segment (unseenet#323), with the
worker and Pyodide's to_js stood in for."""
import asyncio
import importlib
import sys
import types
from pathlib import Path

import pytest

from unseenet.client import session
from unseenet.core import ripening as rp

WEB_PY = Path(__file__).resolve().parent.parent / "web" / "py"


@pytest.fixture
def webbackend(monkeypatch):
    ffi = types.ModuleType("pyodide.ffi")
    ffi.to_js = lambda b: b                  # bytes stand in for a Uint8Array
    monkeypatch.setitem(sys.modules, "pyodide", types.ModuleType("pyodide"))
    monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi)
    monkeypatch.syspath_prepend(str(WEB_PY))
    # each test gets its own Session methods and ripening.available
    monkeypatch.setattr(session.Session, "_prove_segment",
                        getattr(session.Session, "_prove_segment", None), raising=False)
    monkeypatch.setattr(rp, "available", rp.available)
    return importlib.import_module("webbackend")


class Worker:
    """The JS side: ``prove`` gives a promise of the result, ``stop`` ends it."""

    def __init__(self, result: bytes | None = None):
        self.result, self.asked, self.stopped = result, [], 0
        self.future: asyncio.Future | None = None

    def prove(self, seed, x, bits, t):
        self.asked.append((seed, x, bits, t))
        self.future = asyncio.get_running_loop().create_future()
        if self.result is not None:
            self.future.set_result(Bytes(self.result))
        return self.future

    def stop(self):
        self.stopped += 1
        if self.future and not self.future.done():
            self.future.set_result(None)


class Bytes:
    """A Uint8Array as Pyodide hands it to Python."""

    def __init__(self, data):
        self.data = data

    def to_bytes(self):
        return self.data


def has_seam() -> bool:
    return session.Session._prove_segment is not None


@pytest.mark.asyncio
async def test_segments_are_proved_in_the_worker(webbackend):
    if not has_seam():
        pytest.skip("a core before 0.4.2 has no Session._prove_segment")
    pk = bytes(range(32))
    y, pi = bytes([1]) * rp.FORM_SIZE, bytes([2]) * rp.FORM_SIZE
    worker = Worker(y + pi)
    webbackend._patch_ripening(worker)
    seg = await session.Session._prove_segment(None, pk, rp.GENERATOR, 1 << 24)
    assert worker.asked == [(rp.seed(pk), rp.GENERATOR, rp.DISC_BITS, 1 << 24)]
    assert (seg.t, seg.y, seg.pi) == (1 << 24, y, pi)


@pytest.mark.asyncio
async def test_removing_the_run_file_stops_the_worker(webbackend, tmp_path):
    if not has_seam():
        pytest.skip("a core before 0.4.2 has no Session._prove_segment")
    run_file = tmp_path / "run"
    run_file.touch()
    worker = Worker()                         # never finishes by itself
    webbackend._patch_ripening(worker)
    proving = asyncio.ensure_future(
        session.Session._prove_segment(None, bytes(32), rp.GENERATOR, 1 << 24, run_file))
    await asyncio.sleep(0.1)
    assert not proving.done()
    run_file.unlink()                         # ClientRuntime.stop_ripening()
    assert await asyncio.wait_for(proving, 3) is None    # stopped: started again later
    assert worker.stopped == 1


def test_a_core_without_the_seam_does_not_ripen(webbackend, monkeypatch):
    monkeypatch.delattr(session.Session, "_prove_segment", raising=False)
    webbackend._patch_ripening(Worker())
    assert rp.available() is False            # as before: the web client cannot ripen
