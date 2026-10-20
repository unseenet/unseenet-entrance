"""Open a tunnel through a running entrance and ask the node for CAPABILITIES.

    python tests/smoke_tunnel.py http://127.0.0.1:8043 <onion>
"""
import asyncio
import sys
import time

import aiohttp


async def main(base: str, onion: str) -> None:
    t = time.monotonic()
    url = base.replace("http", "ws", 1).rstrip("/") + f"/onntp?node={onion}"
    async with aiohttp.ClientSession() as s, s.ws_connect(url) as ws:
        buf = b""
        while b"\r\n" not in buf:
            buf += (await ws.receive()).data
        print(f"greeting after {time.monotonic() - t:.1f}s:", buf.decode().strip())
        await ws.send_bytes(b"CAPABILITIES\r\n")
        buf = b""
        while not buf.endswith(b"\r\n.\r\n"):
            buf += (await ws.receive()).data
        print(buf.decode()[:400])


asyncio.run(main(sys.argv[1], sys.argv[2]))
