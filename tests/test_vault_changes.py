"""#32: the page offers to save the vault on real changes and new key levels,
not on every ripening checkpoint. webbackend tells them apart."""
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

WEB_PY = Path(__file__).resolve().parent.parent / "web" / "py"
KEY = "ed25519:abc"


@pytest.fixture
def wb(monkeypatch):
    ffi = types.ModuleType("pyodide.ffi")
    ffi.to_js = lambda b: b
    monkeypatch.setitem(sys.modules, "pyodide", types.ModuleType("pyodide"))
    monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi)
    monkeypatch.syspath_prepend(str(WEB_PY))
    return importlib.import_module("webbackend")


def _vault(*ts, published=0, **rest):
    segs = [{"t": t, "y": "y", "pi": "pi"} for t in ts]
    return {"identities": {"me": {}}, **rest,
            "ripening": {KEY: {"segments": segs, "published": published}}}


def test_kinds_of_change(wb):
    seen = {}
    assert wb.vault_change(_vault(1000), seen) == ("data", None)            # nothing seen yet
    assert wb.vault_change(_vault(1000, 5000), seen) == ("ripening", None)  # a checkpoint
    assert wb.vault_change(_vault(1000, 5000, 2**26), seen) == ("level", 0)  # level 0 reached
    assert wb.vault_change(_vault(1000, 5000, 2**26, published=2**26 + 6000), seen) == ("level", 0)
    v = _vault(1000, 5000, 2**26, published=2**26 + 6000, subscribed=["dk.snak"])
    assert wb.vault_change(v, seen) == ("data", 0)                          # you followed a group
    assert wb.vault_change(v, seen) == ("ripening", 0)                      # saved again, nothing new


def test_the_page_hears_the_kind(wb, monkeypatch, tmp_path):
    """Vault.open records what it opened; each save says what changed since."""
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    from unseenet.core import vault

    monkeypatch.setattr(vault.Vault, "save", vault.Vault.save)
    monkeypatch.setattr(vault.Vault, "open", vault.Vault.open)
    said = []
    monkeypatch.setattr(wb, "_post", lambda s: said.append(json.loads(s)))
    wb._patch_vault()
    v = vault.Vault.open(tmp_path / "v.vault", "pw", ops=1, mem=8192 * 1024)
    v.data["ripening"] = {KEY: {"segments": [{"t": 500, "y": "y", "pi": "pi"}], "published": 0}}
    v.save()
    v.data["ripening"][KEY]["segments"].append({"t": 2**26, "y": "y", "pi": "pi"})
    v.save()
    v.data["name"] = "x"
    v.save()
    assert [(m["kind"], m["level"]) for m in said] == [("ripening", None), ("level", 0), ("data", 0)]
    again = vault.Vault.open(tmp_path / "v.vault", "pw", ops=1, mem=8192 * 1024)
    again.save()
    assert said[-1]["kind"] == "ripening"                  # opened and saved unchanged: no offer
