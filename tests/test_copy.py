"""Relaying the copy of an identity (#53, UN-RFC 0043 section 4.5): only a v3 onion on
port 9119, one copy per session at a time, at most 2 MiB each way, and off when the
operator says so - which the entrance information tells."""
import asyncio
import secrets

import pytest
import pytest_asyncio
from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from unseenet_entrance import server as srv

ONION = "a" * 56 + ".onion"


@pytest_asyncio.fixture
async def setup(monkeypatch, tmp_path):
    ports = []

    async def echo(reader, writer):
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
        writer.close()
    device = await asyncio.start_server(echo, "127.0.0.1", 0)
    port = device.sockets[0].getsockname()[1]

    async def fake_tunnel(cfg, onion, credentials=None, port_=srv.ONNTP_PORT, **kw):
        ports.append(kw.get("port", port_))
        return await asyncio.open_connection("127.0.0.1", port)
    monkeypatch.setattr(srv, "open_tunnel", fake_tunnel)
    cfg = srv.Config(nodes="any", web_dir=tmp_path)
    app = srv.make_app(cfg)
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client, cfg, ports
    await client.close()
    device.close()


def tag():
    return secrets.token_urlsafe(16)


@pytest.mark.asyncio
async def test_a_copy_is_relayed_to_port_9119(setup):
    client, cfg, ports = setup
    ws = await client.ws_connect(f"/copy?onion={ONION}&session={tag()}")
    await ws.send_bytes(b"UNSEENET-COPY-1")
    msg = await ws.receive(timeout=5)
    assert msg.type == WSMsgType.BINARY and msg.data == b"UNSEENET-COPY-1"
    assert ports == [srv.COPY_PORT]
    await ws.close()


@pytest.mark.asyncio
async def test_one_copy_per_session_at_a_time(setup):
    client, cfg, ports = setup
    s = tag()
    first = await client.ws_connect(f"/copy?onion={ONION}&session={s}")
    await first.send_bytes(b"x")
    await first.receive(timeout=5)
    second = await client.get(f"/copy?onion={ONION}&session={s}")
    assert second.status == 429
    other = await client.ws_connect(f"/copy?onion={ONION}&session={tag()}")   # another session
    await other.close()
    await first.close()
    await asyncio.sleep(0.1)
    again = await client.ws_connect(f"/copy?onion={ONION}&session={s}")       # once it closed
    await again.close()


@pytest.mark.asyncio
async def test_refused_without_a_v3_onion_or_a_session(setup):
    client, cfg, ports = setup
    assert (await client.get(f"/copy?onion=example.com&session={tag()}")).status == 400
    assert (await client.get(f"/copy?onion={ONION}")).status == 400
    assert ports == []


@pytest.mark.asyncio
async def test_at_most_two_mib_each_way(setup, monkeypatch):
    client, cfg, ports = setup
    monkeypatch.setattr(srv, "COPY_BYTES", 1000)
    ws = await client.ws_connect(f"/copy?onion={ONION}&session={tag()}")
    await ws.send_bytes(b"y" * 600)
    await ws.receive(timeout=5)
    await ws.send_bytes(b"y" * 600)                     # past 1000 going up: closed
    msg = await ws.receive(timeout=5)
    assert msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING)


@pytest.mark.asyncio
async def test_the_operator_can_turn_it_off_and_it_is_said(setup, monkeypatch):
    client, cfg, ports = setup
    info = await (await client.get("/.well-known/unseenet-entrance")).json()
    assert info["copy"] is True
    cfg.copy = False
    assert (await client.get(f"/copy?onion={ONION}&session={tag()}")).status == 403
    monkeypatch.setenv("ENTRANCE_COPY", "off")
    off = srv.Config.from_env()
    assert off.copy is False and off.info["copy"] is False
