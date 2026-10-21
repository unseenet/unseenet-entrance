#!/usr/bin/env python3
"""Build the web client: unseenet-gui's page with its backend in the browser.

    python3 tools/build_web.py [--core PATH] [--gui PATH]

PATH is a checkout of unseenet / unseenet-gui; without one, the main branch is
cloned from GitHub. Writes into web/:

    index.html      unseenet-gui's page, with shim.js and the open-web bar
    static/         unseenet-gui's static files, unchanged
    pyodide/        Pyodide and the wheels the core needs (pinned, sha256-checked)
    py/bundle.zip   the unseenet core, unseenet_gui, their pure-Python
                    dependencies, webbackend.py and webtransport.py

Everything the page runs is served by the entrance itself: nothing is loaded
from anywhere else at run time (UN-RFC 0043 section 6.4). Uses only the
standard library, so it runs on a fresh server.
"""
from __future__ import annotations

import argparse
import re
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
CACHE = Path.home() / ".cache" / "unseenet-entrance"

PYODIDE_VERSION = "314.0.7"
PYODIDE_URL = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"
PYODIDE_CORE = ["pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip",
                "pyodide-lock.json"]
PYODIDE_PACKAGES = ["pynacl", "aiohttp"]          # with their dependencies, from the lock file

# Pure-Python dependencies of the core that Pyodide does not ship: (url, sha256, top-level dirs)
WHEELS = [
    ("https://files.pythonhosted.org/packages/3b/23/2c2cef1b4313c55d1713201acd4ee2043fb2cf22e546ca9d36bf4317faea/python_socks-3.1.1-py3-none-any.whl",
     "327e0d6378702c73a7790bf732e9f01392f17b48c7348a50b5bd1f710c2df1be", ["python_socks"]),
    ("https://files.pythonhosted.org/packages/b3/81/4da04ced5a082363ecfa159c010d200ecbd959ae410c10c0264a38cac0f5/markdown_it_py-4.2.0-py3-none-any.whl",
     "9f7ebbcd14fe59494226453aed97c1070d83f8d24b6fc3a3bcf9a38092641c4a", ["markdown_it"]),
    ("https://files.pythonhosted.org/packages/b3/38/89ba8ad64ae25be8de66a6d463314cf1eb366222074cfda9ee839c56a4b4/mdurl-0.1.2-py3-none-any.whl",
     "84008a41e51615a49fc9966191ff91509e3c40b939176e643fd50a5c2196b8f8", ["mdurl"]),
]

REPOS = {"core": "https://github.com/unseenet/unseenet.git",
         "gui": "https://github.com/unseenet/unseenet-gui.git"}


def fetch(url: str, sha256: str | None = None) -> bytes:
    """Download once into the cache; check the hash when one is known."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / hashlib.sha256(url.encode()).hexdigest()[:24]
    if cached.exists():
        data = cached.read_bytes()
    else:
        with urllib.request.urlopen(url, timeout=120) as r:
            data = r.read()
    if sha256 and hashlib.sha256(data).hexdigest() != sha256:
        cached.unlink(missing_ok=True)
        sys.exit(f"sha256 mismatch for {url}")
    cached.write_bytes(data)
    return data


def source(kind: str, path: str | None, tmp: Path) -> Path:
    if path:
        return Path(path).resolve()
    dest = tmp / kind
    subprocess.run(["git", "clone", "-q", "--depth", "1", REPOS[kind], str(dest)], check=True)
    return dest


def build_pyodide() -> None:
    out = WEB / "pyodide"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    for name in PYODIDE_CORE:
        (out / name).write_bytes(fetch(PYODIDE_URL + name))
    lock = json.loads((out / "pyodide-lock.json").read_text())
    pkgs = lock["packages"]
    todo, seen = list(PYODIDE_PACKAGES), set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        info = pkgs[name]
        (out / info["file_name"]).write_bytes(fetch(PYODIDE_URL + info["file_name"], info["sha256"]))
        todo.extend(info.get("depends", []))
    print(f"pyodide {PYODIDE_VERSION}: {len(seen)} packages ({', '.join(sorted(seen))})")


def build_bundle(core: Path, gui: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for pkg, base in (("unseenet", core / "src" / "unseenet"), ("unseenet_gui", gui / "src" / "unseenet_gui")):
            for f in sorted(base.rglob("*")):
                rel = f.relative_to(base)
                if f.is_dir() or "__pycache__" in rel.parts or rel.parts[0] == "static":
                    continue
                if f.suffix in (".py", ".json", ".txt"):
                    z.write(f, f"{pkg}/{rel.as_posix()}")
        for f in sorted((WEB / "py").glob("*.py")):
            z.write(f, f.name)
        for url, sha, tops in WHEELS:
            with zipfile.ZipFile(io.BytesIO(fetch(url, sha))) as w:
                for name in w.namelist():
                    if name.split("/")[0] in tops:
                        z.writestr(name, w.read(name))
    (WEB / "py" / "bundle.zip").write_bytes(buf.getvalue())
    print(f"bundle.zip: {len(buf.getvalue()) // 1024} KiB")


def build_page(gui: Path) -> None:
    static = gui / "src" / "unseenet_gui" / "static"
    shutil.rmtree(WEB / "static", ignore_errors=True)
    shutil.copytree(static, WEB / "static")
    html = (static / "index.html").read_text()
    anchor = '<link rel="stylesheet" href="/static/app.css">'
    if anchor not in html:
        sys.exit("unseenet-gui's index.html changed: update build_page()")
    html = html.replace(anchor, anchor + '\n<link rel="stylesheet" href="/entrance.css">\n'
                        '<script src="/shim.js"></script>')
    html = html.replace("<title>Unseenet</title>", "<title>Unseenet on the web</title>")
    # an app for the home screen (the manifest, and sw.js from build_sw)
    html = html.replace("</head>", '<link rel="manifest" href="/app/manifest.webmanifest">\n'
                        '<meta name="theme-color" content="#10302a">\n'
                        '<link rel="apple-touch-icon" href="/app/apple-touch-icon.png">\n'
                        '<meta name="apple-mobile-web-app-capable" content="yes">\n'
                        '<meta name="apple-mobile-web-app-title" content="Unseenet">\n</head>')
    (WEB / "index.html").write_text(html)


def build_sw() -> None:
    """sw.js, with a version that changes whenever anything it keeps changes."""
    h = hashlib.sha256()
    for f in sorted(p for d in ("static", "pyodide", "py", "app") for p in (WEB / d).rglob("*") if p.is_file()):
        h.update(f.name.encode() + f.read_bytes())
    for n in ("index.html", "shim.js", "worker.js", "entrance.css"):
        h.update((WEB / n).read_bytes())
    (WEB / "sw.js").write_text((WEB / "sw.template.js").read_text().replace("__VERSION__", h.hexdigest()[:16]))


# --- the jump page: one file, no imports (UN-RFC 0043 section 8.7) ------------

JS = ROOT / "js"
JUMP_MODULES = [JS / "vendor" / "noble.js", JS / "encoding.js", JS / "article.js",
                JS / "entrances.js", ROOT / "jump" / "jump.js"]
_IMPORT = re.compile(r'^import\s*\{([^}]*)\}\s*from\s*"([^"]+)";?\s*$', re.M)
_EXPORT_DECL = re.compile(r"^export\s+((?:async\s+)?function\*?|const|let|class)\s+([A-Za-z_$][\w$]*)", re.M)
_EXPORT_LIST = re.compile(r"export\s*\{([^}]*)\};?")


def _module_key(path: str) -> str:
    return Path(path).stem


def bundle_modules(files: list[Path]) -> str:
    """Join ES modules into one script, each in its own scope (a tiny bundler)."""
    out = ["const __m = {};"]
    for f in files:
        code = f.read_text()
        names: list[tuple[str, str]] = []           # (exported name, local name)

        def imp(m):
            parts = []
            for item in m.group(1).split(","):
                item = item.strip()
                if not item:
                    continue
                src, _, alias = item.partition(" as ")
                parts.append(f"{src.strip()}: {alias.strip()}" if alias else src.strip())
            return f"const {{ {', '.join(parts)} }} = __m[{_module_key(m.group(2))!r}];"
        code = _IMPORT.sub(imp, code)

        def decl(m):
            names.append((m.group(2), m.group(2)))
            return m.group(1) + " " + m.group(2)
        code = _EXPORT_DECL.sub(decl, code)

        def lst(m):
            for item in m.group(1).split(","):
                item = item.strip()
                if item:
                    local, _, exported = item.partition(" as ")
                    names.append(((exported or local).strip(), local.strip()))
            return ""
        code = _EXPORT_LIST.sub(lst, code)
        exports = ", ".join(f"{e}: {l}" for e, l in names)
        out.append(f"__m[{_module_key(str(f))!r}] = (() => {{\n{code}\nreturn {{ {exports} }};\n}})();")
    return "\n".join(out)


def build_jump(owner: str | None, group: str | None) -> None:
    html = (ROOT / "jump" / "jump.html").read_text()
    script = bundle_modules(JUMP_MODULES).replace("</script", "<\\/script")
    html = html.replace("<!--JUMP-SCRIPT-->", f'<script type="module" id="jump">\n{script}\n</script>')
    if owner and group:          # a test list instead of the project's (never in a release)
        html = html.replace('data-owner="" data-group=""', f'data-owner="{owner}" data-group="{group}"')
    (WEB / "unseenet-entrances.template.html").write_text(html)
    print(f"jump page template: {len(html) // 1024} KiB")


# --- the client in a file (UN-RFC 0043 section 8.7) ----------------------------

FILE_MODULES = [JS / "vendor" / "noble.js", JS / "encoding.js", JS / "article.js", JS / "entrances.js"]


def build_file(bundle_from: str, owner: str | None, group: str | None) -> Path:
    """One HTML file: the GUI page, Pyodide, the core and the signed list of
    entrances. Opened from disk, it uses an entrance only as a tunnel."""
    import base64
    bundle = json.loads(urllib.request.urlopen(
        bundle_from.rstrip("/") + "/.well-known/unseenet-entrances.json", timeout=30).read())
    if not bundle.get("manifest"):
        sys.exit(f"{bundle_from} has no copy of the list of entrances yet")
    static = WEB / "static"
    icon = lambda n: "data:image/svg+xml;base64," + base64.b64encode((static / n).read_bytes()).decode()  # noqa: E731
    app_js = (static / "app.js").read_text()
    for n in ("icon.svg", "icon-ink.svg"):
        app_js = app_js.replace(f'"/static/{n}"', json.dumps(icon(n)))
    safe = lambda s: s.replace("</script", "<\\/script")            # noqa: E731
    modules = bundle_modules(FILE_MODULES).replace("import.meta.url", "self.location.href")
    files = {p.name: p for p in sorted((WEB / "pyodide").iterdir()) if p.is_file()}
    files["bundle.zip"] = WEB / "py" / "bundle.zip"
    files["worker.js"] = WEB / "worker.js"
    conf = {"bundle": bundle, "files": sorted(files)}
    if owner and group:
        conf.update(owner=owner, group=group)
    parts = ["<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             '<meta name="referrer" content="no-referrer">',
             "<title>Unseenet</title>",
             f'<link rel="icon" href="{icon("icon.svg")}" type="image/svg+xml">',
             f"<style>\n{(static / 'app.css').read_text()}\n{(WEB / 'entrance.css').read_text()}\n</style>",
             f"<script>window.UNSEENET_FILE = {safe(json.dumps(conf))};</script>",
             f"<script>\n{safe(modules)}\n</script>",
             f"<script>\n{safe((WEB / 'shim.js').read_text())}\n</script>",
             "</head>", "<body>",
             "<noscript>Unseenet needs JavaScript. Everything runs here, in this file.</noscript>",
             '<div id="root"></div>',
             f"<script>\n{safe(app_js)}\n</script>"]
    for name, path in sorted(files.items()):
        parts.append(f'<script type="application/octet-stream" data-file="{name}">'
                     f"{base64.b64encode(path.read_bytes()).decode()}</script>")
    parts += ["</body>", "</html>"]
    out = WEB / "unseenet-web.html"
    out.write_text("\n".join(parts))
    print(f"client in a file: {out.stat().st_size // (1024 * 1024)} MiB")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--core", help="unseenet checkout (default: clone from GitHub)")
    ap.add_argument("--gui", help="unseenet-gui checkout (default: clone from GitHub)")
    ap.add_argument("--list-owner", help="test only: trust this key's list instead of the project's")
    ap.add_argument("--list-group", help="test only: the group of that list")
    ap.add_argument("--jump-only", action="store_true", help="build only the jump page")
    ap.add_argument("--file-from", metavar="ENTRANCE",
                    help="also build the client in a file, with the signed list from this entrance")
    args = ap.parse_args()
    build_jump(args.list_owner, args.list_group)
    if args.jump_only:
        return
    with tempfile.TemporaryDirectory() as tmp:
        core = source("core", args.core, Path(tmp))
        gui = source("gui", args.gui, Path(tmp))
        build_page(gui)
        build_pyodide()
        build_bundle(core, gui)
        build_sw()
    if args.file_from:
        build_file(args.file_from, args.list_owner, args.list_group)
    print("web client built in", WEB)


if __name__ == "__main__":
    main()
