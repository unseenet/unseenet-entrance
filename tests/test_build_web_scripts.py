"""#50: the single offline file carries every GUI script, in index.html's order."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_web", ROOT / "tools" / "build_web.py")
bw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bw)


def test_every_script_in_order(tmp_path):
    (tmp_path / "index.html").write_text(
        '<script src="/static/app.js" defer></script>\n<script src="/static/identity.js" defer></script>\n'
        '<script src="/static/js/notify.js" defer></script>')
    (tmp_path / "js").mkdir()
    (tmp_path / "app.js").write_text('const A = "/static/icon.svg";')
    (tmp_path / "identity.js").write_text("function openRestore() {}")
    (tmp_path / "js" / "notify.js").write_text("function notifySoon() {}")
    assert bw.gui_scripts(tmp_path) == ["app.js", "identity.js", "js/notify.js"]
    out = bw.inline_scripts(tmp_path, lambda n: "data:x")
    assert out.index("const A") < out.index("openRestore") < out.index("notifySoon")
    assert json.dumps("data:x") in out and "/static/icon.svg" not in out


def test_without_an_index_it_is_app_js(tmp_path):
    assert bw.gui_scripts(tmp_path) == ["app.js"]
