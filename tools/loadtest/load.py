"""Open many web-client sessions against an entrance and time them.

    python load.py wss://HOST:PORT --node <onion> --sessions 1000 --rate 20

A session is what the web client keeps open: a main connection that asks for
something now and then, and a watch connection that only listens (the core's
Session keeps one connection per node plus one for watching).

--mode stub   talks to stubs.py's node (FETCH/WATCH)
--mode real   talks to a real node: CAPABILITIES, DATE now and then, XUN-WATCH *
              Use only a test node, and keep the numbers small: every tunnel
              is an onion circuit on the real Tor network.
"""
from __future__ import annotations

import argparse
import asyncio
import ssl
import statistics
import time

import aiohttp

A = None
stats = {"open": 0, "opened": 0, "failed": {}, "connect": [], "reply": [], "closed_early": 0}


def fail(why: str) -> None:
    stats["failed"][why] = stats["failed"].get(why, 0) + 1


async def read_until(ws, done) -> bytes:
    buf = b""
    while not done(buf):
        msg = await ws.receive(timeout=A.timeout)
        if msg.type != aiohttp.WSMsgType.BINARY:
            raise ConnectionError(f"ws {msg.type.name} {msg.extra or ''}".strip())
        buf += msg.data
    return buf


def line_done(b: bytes) -> bool:
    return b.endswith(b"\n")


def multi_done(b: bytes) -> bool:
    return b.endswith(b"\r\n.\r\n") or b == b".\r\n"


async def tunnel(http: aiohttp.ClientSession, watch: bool, stop: asyncio.Event) -> None:
    t0 = time.monotonic()
    try:
        ws = await http.ws_connect(f"{A.url}/onntp?node={A.node}", ssl=A.ssl,
                                   timeout=aiohttp.ClientWSTimeout(ws_close=5), heartbeat=None,
                                   max_msg_size=0)
    except aiohttp.WSServerHandshakeError as exc:
        fail(f"http {exc.status}")
        return
    except Exception as exc:      # noqa: BLE001
        fail(type(exc).__name__)
        return
    try:
        await read_until(ws, line_done)           # the greeting
        stats["connect"].append(time.monotonic() - t0)
        stats["open"] += 1
        stats["opened"] += 1
        if A.mode == "real":
            await ws.send_bytes(b"CAPABILITIES\r\n")
            await read_until(ws, multi_done)
        if watch:
            await ws.send_bytes(b"WATCH\r\n" if A.mode == "stub" else b"XUN-WATCH *\r\n")
            while not stop.is_set():
                msg = await ws.receive()
                if msg.type != aiohttp.WSMsgType.BINARY:
                    stats["closed_early"] += 1
                    return
        else:
            ask = f"FETCH {A.size}\r\n".encode() if A.mode == "stub" else b"DATE\r\n"
            done = multi_done if A.mode == "stub" else line_done
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), A.interval)
                    break
                except asyncio.TimeoutError:
                    pass
                t1 = time.monotonic()
                await ws.send_bytes(ask)
                await read_until(ws, done)
                stats["reply"].append(time.monotonic() - t1)
    except Exception as exc:      # noqa: BLE001
        if stats["open"] and not stop.is_set():
            stats["closed_early"] += 1
        fail(type(exc).__name__ if not isinstance(exc, ConnectionError) else str(exc)[:40])
    finally:
        if not ws.closed:
            await ws.close()
        stats["open"] = max(0, stats["open"] - 1)


def pct(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


async def reporter(t0: float) -> None:
    while True:
        await asyncio.sleep(5)
        c, r = stats["connect"], stats["reply"][-2000:]
        print(f"{time.monotonic() - t0:6.0f}s open={stats['open']:5d} opened={stats['opened']:5d} "
              f"connect p50={pct(c, .5):.2f} p95={pct(c, .95):.2f} max={max(c, default=0):.2f}s "
              f"reply p50={pct(r, .5) * 1000:.0f} p95={pct(r, .95) * 1000:.0f}ms "
              f"early={stats['closed_early']} failed={stats['failed']}", flush=True)


async def main() -> None:
    stop = asyncio.Event()
    conn = aiohttp.TCPConnector(limit=0, ssl=A.ssl)
    async with aiohttp.ClientSession(connector=conn) as http:
        t0 = time.monotonic()
        rep = asyncio.create_task(reporter(t0))
        tasks = []
        for i in range(A.sessions):
            tasks.append(asyncio.create_task(tunnel(http, False, stop)))
            if A.watch:
                tasks.append(asyncio.create_task(tunnel(http, True, stop)))
            await asyncio.sleep(1 / A.rate)
        print(f"all {A.sessions} sessions started after {time.monotonic() - t0:.0f}s; holding {A.hold}s",
              flush=True)
        await asyncio.sleep(A.hold)
        stop.set()
        await asyncio.sleep(1)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        rep.cancel()
        c = stats["connect"]
        print(f"DONE sessions={A.sessions} tunnels_opened={stats['opened']} failed={stats['failed']} "
              f"early={stats['closed_early']} connect p50={pct(c, .5):.2f} p95={pct(c, .95):.2f} "
              f"max={max(c, default=0):.2f}s replies={len(stats['reply'])} "
              f"reply p50={pct(stats['reply'], .5) * 1000:.0f} p95={pct(stats['reply'], .95) * 1000:.0f}ms",
              flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("url", help="wss://host:port (no path)")
    p.add_argument("--node", default="a" * 56 + ".onion")
    p.add_argument("--mode", choices=("stub", "real"), default="stub")
    p.add_argument("--sessions", type=int, default=100)
    p.add_argument("--rate", type=float, default=20, help="new sessions per second")
    p.add_argument("--hold", type=float, default=60)
    p.add_argument("--interval", type=float, default=20, help="seconds between requests")
    p.add_argument("--size", type=int, default=20000, help="bytes per stub reply")
    p.add_argument("--no-watch", dest="watch", action="store_false")
    p.add_argument("--timeout", type=float, default=120)
    A = p.parse_args()
    A.ssl = ssl.create_default_context()
    A.ssl.check_hostname = False
    A.ssl.verify_mode = ssl.CERT_NONE
    asyncio.run(main())
