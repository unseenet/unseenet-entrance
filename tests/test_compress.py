"""The web client served compressed (#22): .gz copies beside the files, made from
checked files, and served to browsers that accept gzip."""
import asyncio
import gzip

from aiohttp.test_utils import TestClient, TestServer

from unseenet_entrance import server
from unseenet_entrance.compress import is_encoded_copy, precompress


def _tree(tmp_path):
    (tmp_path / "pyodide").mkdir()
    big = b"unseenet " * 20000                         # compresses very well
    (tmp_path / "pyodide" / "pyodide.asm.wasm").write_bytes(big)
    (tmp_path / "small.js").write_bytes(b"x" * 100)    # under 1 KB: left alone
    (tmp_path / "pic.png").write_bytes(b"p" * 5000)    # already compressed: skipped
    (tmp_path / "index.html").write_bytes(b"<p>hi</p>" * 400)
    return big


def test_copies_only_where_they_help(tmp_path):
    big = _tree(tmp_path)
    assert precompress(tmp_path) >= 2
    gz = tmp_path / "pyodide" / "pyodide.asm.wasm.gz"
    assert gzip.decompress(gz.read_bytes()) == big and gz.stat().st_size < len(big) // 10
    assert not (tmp_path / "small.js.gz").exists()
    assert not (tmp_path / "pic.png.gz").exists()
    assert is_encoded_copy(gz) and not is_encoded_copy(tmp_path / "index.html")
    first = gz.read_bytes()
    precompress(tmp_path)                              # the same bytes again (mtime=0)
    assert gz.read_bytes() == first
    assert not (tmp_path / "pyodide" / "pyodide.asm.wasm.gz.gz").exists()


def test_served_compressed_when_accepted(tmp_path):
    big = _tree(tmp_path)
    precompress(tmp_path)

    async def run():
        cfg = server.Config(web_dir=tmp_path)
        async with TestClient(TestServer(server.make_app(cfg))) as c:
            r = await c.get("/pyodide/pyodide.asm.wasm", headers={"Accept-Encoding": "gzip"},
                            auto_decompress=False)
            assert r.status == 200 and r.headers.get("Content-Encoding") == "gzip"
            raw = await r.read()
            assert len(raw) < len(big) // 10 and gzip.decompress(raw) == big
            r = await c.get("/pyodide/pyodide.asm.wasm", headers={"Accept-Encoding": "identity"},
                            auto_decompress=False)
            assert "Content-Encoding" not in r.headers and await r.read() == big
    asyncio.run(run())
