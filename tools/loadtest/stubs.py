"""Stand-ins for Tor and a node, to measure the entrance on its own.

    python stubs.py [--socks 19050] [--node 11900] [--delay 0]

- a SOCKS5 server (username/password accepted, as the entrance sends random
  ones) that connects every request to the stub node, after ``--delay``
  seconds (to imitate building an onion circuit);
- a stub node speaking just enough NNTP-like text: a greeting, ``FETCH <n>``
  answers n bytes and a final ".", ``WATCH`` sends a line every ``--watch``
  seconds, ``QUIT`` closes.

Nothing here touches the real Tor network.
"""
from __future__ import annotations

import argparse
import asyncio
import struct

ARGS = None


async def pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        w.close()


async def socks(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        ver, n = await reader.readexactly(2)
        methods = await reader.readexactly(n)
        method = 2 if 2 in methods else 0
        writer.write(bytes([5, method]))
        if method == 2:
            await reader.readexactly(1)
            ulen = (await reader.readexactly(1))[0]
            await reader.readexactly(ulen)
            plen = (await reader.readexactly(1))[0]
            await reader.readexactly(plen)
            writer.write(b"\x01\x00")
        _, cmd, _, atyp = await reader.readexactly(4)
        if atyp == 3:
            await reader.readexactly((await reader.readexactly(1))[0])
        elif atyp == 1:
            await reader.readexactly(4)
        else:
            await reader.readexactly(16)
        await reader.readexactly(2)
        if ARGS.delay:
            await asyncio.sleep(ARGS.delay)
        nr, nw = await asyncio.open_connection("127.0.0.1", ARGS.node)
        writer.write(b"\x05\x00\x00\x01" + bytes(4) + struct.pack(">H", 0))
        await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        writer.close()
        return
    await asyncio.gather(pipe(reader, nw), pipe(nr, writer))


async def node(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    watcher = None
    try:
        writer.write(b"200 stub node ready\r\n")
        while line := await reader.readline():
            parts = line.decode().split()
            if not parts:
                continue
            if parts[0] == "FETCH":
                n = int(parts[1])
                writer.write(b"224 data\r\n" + (b"x" * 78 + b"\r\n") * (n // 80) + b".\r\n")
            elif parts[0] == "WATCH":
                writer.write(b"290 watching\r\n")

                async def tick() -> None:
                    i = 0
                    while True:
                        await asyncio.sleep(ARGS.watch)
                        i += 1
                        writer.write(f"291 alt.test {i} <{i}@stub>\r\n".encode())
                watcher = asyncio.create_task(tick())
            elif parts[0] == "QUIT":
                writer.write(b"205 bye\r\n")
                break
            else:
                writer.write(b"500 what\r\n")
            await writer.drain()
    except (ConnectionError, OSError, ValueError):
        pass
    finally:
        if watcher:
            watcher.cancel()
        writer.close()


async def main() -> None:
    await asyncio.start_server(node, "127.0.0.1", ARGS.node, backlog=4096)
    await asyncio.start_server(socks, "127.0.0.1", ARGS.socks, backlog=4096)
    print(f"stub SOCKS on {ARGS.socks}, stub node on {ARGS.node}, delay {ARGS.delay}s", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--socks", type=int, default=19050)
    p.add_argument("--node", type=int, default=11900)
    p.add_argument("--delay", type=float, default=0.0)
    p.add_argument("--watch", type=float, default=60.0)
    ARGS = p.parse_args()
    asyncio.run(main())
