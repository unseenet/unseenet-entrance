"""Compressed copies of the web client's files, served to browsers that accept them (#22).

aiohttp's ``FileResponse`` serves ``<file>.br`` or ``<file>.gz`` from beside a file
when the browser sends a matching ``Accept-Encoding``, so all that is needed is to
write them. A first visit goes from about 15 MB to about 7.8 MB with gzip
(``pyodide.asm.wasm`` from 9.6 to 3.5 MB).

They are made here from files that were already checked against a signed release
(release.py) or that were just built (tools/build_web.py), never fetched from
anywhere. They are not in ``release.sums``: the browser decompresses before the
service worker hashes a file, so UN-RFC 0043 section 7 is unchanged.
"""

from __future__ import annotations

import gzip
from pathlib import Path

MIN_SIZE = 1024                # smaller files are not worth it
MIN_SAVING = 0.10              # keep a copy only if it is at least 10 % smaller
SKIP = {".gz", ".br", ".zip", ".whl", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif",
        ".woff", ".woff2", ".ico"}   # already compressed
ENCODED = (".gz", ".br")


def is_encoded_copy(path: str | Path) -> bool:
    """A ``.gz`` or ``.br`` copy made here, not a file of the release."""
    return str(path).endswith(ENCODED)


def _brotli():
    try:
        import brotli  # type: ignore[import-not-found]
    except ImportError:
        return None
    return brotli


def precompress(root: Path, files=None) -> int:
    """Write ``.gz`` (and ``.br``, when the ``brotli`` module is installed) beside each
    of ``files`` (paths relative to ``root``; by default every file under it) that is
    over 1 KB and gets at least 10 % smaller. Stale copies of files that changed are
    replaced; copies that would not help are removed. Returns how many were written."""
    root = Path(root)
    if files is None:
        files = [p.relative_to(root).as_posix() for p in root.rglob("*")
                 if p.is_file() and not is_encoded_copy(p)]
    br = _brotli()
    written = 0
    for rel in files:
        src = root / rel
        if not src.is_file() or is_encoded_copy(src):
            continue
        data = src.read_bytes()
        worth = len(data) >= MIN_SIZE and src.suffix.lower() not in SKIP
        encoders = [(".gz", lambda d: gzip.compress(d, compresslevel=9, mtime=0))]
        if br is not None:
            encoders.append((".br", lambda d: br.compress(d, quality=11)))
        for ext, encode in encoders:
            out = src.with_name(src.name + ext)
            packed = encode(data) if worth else None
            if packed is None or len(packed) > len(data) * (1 - MIN_SAVING):
                out.unlink(missing_ok=True)
                continue
            out.write_bytes(packed)
            written += 1
    return written
