"""ENTRANCE_MAX_TOTAL=auto: as many tunnels as the machine carries (#20)."""

from unseenet_entrance import server
from unseenet_entrance.server import Config, auto_max_total

MB = 1 << 20


def test_memory_decides_on_a_small_machine():
    n, bound = auto_max_total(mem=512 * MB, files=65536)
    assert bound == "memory" and n == (512 * MB - server.RESERVED_BYTES) // server.TUNNEL_BYTES
    assert 3000 < n < 4000                     # what the load test held on 512 MB


def test_the_default_open_file_limit_decides_and_is_named():
    n, bound = auto_max_total(mem=1024 * MB, files=1024)
    assert bound == "open files" and n == (1024 - 64) // 2


def test_unknown_machine_falls_back_to_the_old_default():
    server_mem, server_files = server._mem_available, server._open_files
    try:
        server._mem_available = lambda *a: None
        server._open_files = lambda: None
        assert auto_max_total() == (Config.max_total, "default")
    finally:
        server._mem_available, server._open_files = server_mem, server_files


def test_a_number_still_overrides_auto(monkeypatch):
    monkeypatch.setenv("ENTRANCE_MAX_TOTAL", "42")
    assert Config.from_env().max_total == 42
    monkeypatch.setenv("ENTRANCE_MAX_TOTAL", "auto")
    assert Config.from_env().max_total >= 10


def test_meminfo_is_read(tmp_path):
    p = tmp_path / "meminfo"
    p.write_text("MemTotal:  1000000 kB\nMemAvailable:  512000 kB\n")
    assert server._mem_available(str(p)) == 512000 * 1024


def test_tls_connections_get_a_small_read_buffer(monkeypatch):
    """#19: 16 KiB instead of asyncio's 256 KiB per TLS connection."""
    import asyncio.sslproto
    from unseenet_entrance import server
    monkeypatch.setattr(asyncio.sslproto.SSLProtocol, "max_size", 256 * 1024)
    assert server.shrink_tls_buffer() is True
    assert asyncio.sslproto.SSLProtocol.max_size == 16 * 1024
