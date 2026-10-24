"""ENTRANCE_HOME: a name sends pages on to the entrance's one address (one origin)."""
import asyncio

from aiohttp.test_utils import TestClient, TestServer

from unseenet_entrance import server


def test_a_name_points_to_the_address():
    async def run():
        cfg = server.Config(home="https://129.151.209.26")
        async with TestClient(TestServer(server.make_app(cfg))) as c:
            async def get(path, host):
                return await c.get(path, headers={"Host": host}, allow_redirects=False)
            r = await get("/static/app.js?x=1", "entrance.unseenet.net")
            assert r.status == 302 and r.headers["Location"] == "https://129.151.209.26/static/app.js?x=1"
            assert (await get("/.well-known/unseenet-entrance", "entrance.unseenet.net")).status == 200
            assert (await get("/", "129.151.209.26")).status != 302
            assert (await get("/", "abc.onion")).status != 302      # the onion is its own way in
    asyncio.run(run())


def test_a_claim_is_shown_while_asking_to_be_listed(monkeypatch):
    monkeypatch.setenv("ENTRANCE_CLAIM", "unseenet-entrance-claim unseenet/unseenet#9 " + "a" * 32)
    assert server.Config.from_env().info["claim"].endswith("a" * 32)
    monkeypatch.delenv("ENTRANCE_CLAIM")
    assert "claim" not in server.Config.from_env().info
