"""#68: the offline cache beside a vault kept on this device. webbackend tells the page
when the cache was written, gives it out and takes it back before the vault is unlocked,
so reopening the page shows the groups at once (the page keeps it in IndexedDB)."""
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

WEB_PY = Path(__file__).resolve().parent.parent / "web" / "py"


@pytest.fixture
def wb(monkeypatch, tmp_path):
    ffi = types.ModuleType("pyodide.ffi")
    ffi.to_js = lambda b: b
    monkeypatch.setitem(sys.modules, "pyodide", types.ModuleType("pyodide"))
    monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi)
    monkeypatch.syspath_prepend(str(WEB_PY))
    mod = importlib.import_module("webbackend")
    monkeypatch.setattr(mod, "VAULT", tmp_path / "vault" / "unseenet.vault")
    monkeypatch.setattr(mod, "_runtime", None)
    return mod


def test_the_cache_goes_out_and_comes_back_beside_the_vault(wb):
    assert wb.cache_out() is None                              # nothing yet
    wb.vault_in(b"vault bytes")
    wb.cache_in(b"cache bytes")
    assert wb.cache_out() == b"cache bytes"
    assert (wb.VAULT.parent / "unseenet.vault.cache").read_bytes() == b"cache bytes"


def test_not_while_a_vault_is_open(wb, monkeypatch):
    monkeypatch.setattr(wb, "_runtime", types.SimpleNamespace(session=object()))
    with pytest.raises(RuntimeError, match="already open"):
        wb.cache_in(b"x")


def test_the_page_hears_when_the_cache_was_written(wb, monkeypatch, tmp_path):
    """Session.save_offline writes the sidecar "cache": the page is told, and keeps it."""
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    from unseenet.client.session import CACHE_SIDECAR
    from unseenet.core import vault

    assert CACHE_SIDECAR == wb.CACHE                           # the core's name for it
    for name in ("save", "open", "write_sidecar"):
        monkeypatch.setattr(vault.Vault, name, getattr(vault.Vault, name))
    said = []
    monkeypatch.setattr(wb, "_post", lambda s: said.append(json.loads(s)))
    wb._patch_vault()
    v = vault.Vault.open(tmp_path / "v.vault", "pw", ops=1, mem=8192 * 1024)
    v.write_sidecar("other", b"x")
    v.write_sidecar(CACHE_SIDECAR, b"articles")
    assert [m["type"] for m in said if m["type"] == "cache-changed"] == ["cache-changed"]
    assert v.read_sidecar(CACHE_SIDECAR) == b"articles"
