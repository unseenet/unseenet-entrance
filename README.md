<p align="center"><img src="web/icon.svg" alt="" width="64"></p>

<h1 align="center">unseenet-entrance</h1>

<p align="center"><b>A web entrance to <a href="https://unseenet.org">Unseenet</a>: the graphical client in an ordinary browser.</b></p>

> **Pre-alpha.** Neither this nor the Unseenet core has had a security
> review. It is the less hidden way in, on purpose - see below.

A **web entrance** ([UN-RFC 0043](https://unseenet.org/spec/un-rfc-0043/)) lets
someone use Unseenet without installing anything. It has two parts:

- **The entrance** (`src/unseenet_entrance`) - a dumb tunnel. For each browser
  WebSocket it opens one connection over Tor to a node's onion service, on a
  Tor circuit of its own, and copies bytes both ways, unchanged. It only
  connects to known nodes, adds nothing, caches nothing and logs nothing.
- **The web client** - not a third client, but
  [unseenet-gui](https://github.com/unseenet/unseenet-gui) itself. Its page is
  served unchanged, and its backend - the real
  [unseenet](https://github.com/unseenet/unseenet) core and the GUI's own
  server code - runs in the browser, in WebAssembly
  ([Pyodide](https://pyodide.org)) inside a Web Worker. So your vault, your
  keys, signing, signature checks and postage all stay in your browser, and
  every fix to the core or the GUI reaches the web too.

```
browser ─ page (unseenet-gui, unchanged) ─ shim.js ─ worker: Python + unseenet core + GUI backend
                                                         │ WebSocket
                                                     entrance ── Tor ── node (.onion)
```

## When an entrance is blocked

Every entrance serves the project's signed list of entrances, fetched from the
network over Tor (`/.well-known/unseenet-entrances.json`), and the **jump
page** `/unseenet-entrances.html` - one small file with that list inside. Keep
it, or send it to someone: opened from disk it checks the list's signatures
against the project key, shows which entrances answer, opens a random one
that does, and can fetch a newer list and save itself again. The web client
links to it ("Other entrances"). See UN-RFC 0043 section 8.

### The client in a file

`python3 tools/build_web.py --file-from https://an.entrance/` also builds
`web/unseenet-web.html`: the whole web client - the GUI page, Python, the core
and the signed list of entrances - in one file of about 19 MB. Opened from
disk it checks the list, picks an entrance that answers and uses it only as a
tunnel; no page is served, so neither a blocked domain nor a changed page
matters while the file itself is genuine. It runs in Chromium and Firefox.

(Browsers refuse module workers from blobs made by a file on disk, so the
worker starts from a `data:` URL and Pyodide gets its files through a `fetch`
that the worker answers itself.)

## What you get - and what you give up

Groups and nodes still cannot be removed by anyone, and nobody can delete or
change someone else's signed post. But **the entrance sees your IP address**,
and you trust the code it serves on every visit. The page says so at all
times. If you need to hide that you use Unseenet, or who you are, use the
[real client](https://unseenet.org) instead.

Your vault lives only in the browser tab: **Open vault file** and **Save vault
file** in the bar at the top move it to and from a file.

## Run an entrance

On Debian 12/13 or Ubuntu 24.04+ (a free cloud VM is enough), with ports 80
and 443 open to the internet:

```sh
curl -fsSLO https://raw.githubusercontent.com/unseenet/unseenet-entrance/main/deploy/install.sh
sudo sh install.sh
```

That installs Tor, gives the entrance an onion address, installs the newest
signed version of the web client, and gets a Let's Encrypt certificate for the machine's **public IP
address** - no domain needed; the 6-day certificates renew by themselves.
`TLS=domain:entrance.example.org` uses a name instead, `TLS=none` plain HTTP
on port 8043 (a LAN test). See [docs/certificates.md](docs/certificates.md).
Never put it behind a CDN that ends TLS on someone else's machine
(UN-RFC 0043 section 5).

## New versions

Every entrance serves the same build of the web client, made by
[this repository's CI](.github/workflows/web.yml) and posted in the project
channel `unseenet.releases` with the SHA-256 of every file (UN-RFC 0043
sections 7.2-7.3):

- **The app** on a phone or computer shows *New version* when there is one,
  and updates only when you say so. It takes the files from any entrance that
  has that version and uses them only when every one matches the signed post.
  It never goes back to an older version, and warns if the entrance serves a
  service worker that is in no signed version.
- **The entrance** updates the web client it serves by itself, the same way:
  from the release here, or file by file from other entrances. Its own code
  is never updated by itself: run `install.sh` again for that.
  `ENTRANCE_AUTO_UPDATE=off` in `/etc/unseenet-entrance.env` stops it.

Neither needs GitHub: the posts are read through the network, and the files
can come from any entrance.

## A mirror of the releases

An entrance can also mirror Unseenet's own releases, so that clients can update
over Tor when GitHub is blocked: `MIRROR=portable sh install.sh` (the portable
zips and the USB zip, about 300 MB per channel) or `MIRROR=all` (also a stick's
update and image, about 1.1 GB). It follows `unseenet.releases`, fetches each
new version from GitHub or another mirror, checks every file against the
signed checksums, keeps only the newest, and serves it at `/mirror/<channel>/`
on its onion address and its open address. Clients find it in the signed list
of entrances and use nothing that does not match the signature. A build of your own (`BUILD=local sh install.sh`)
runs, but the app says it is not signed.

## Develop

```sh
python3 -m venv .venv && .venv/bin/pip install -e .
python3 tools/build_web.py --core ../unseenet --gui ../unseenet-gui   # or without: clones from GitHub
ENTRANCE_SOCKS=127.0.0.1:9050 .venv/bin/unseenet-entrance               # needs a running Tor
# open http://127.0.0.1:8043/
```

A lab with two entrances and signed test versions, without the network:
`tests/lab_updates.py` (see its docstring).

`tools/build_web.py` puts unseenet-gui's page, a pinned Pyodide (sha256-checked)
and a zip of the Python code into `web/`; at run time nothing is loaded from
anywhere else. `web/py/webbackend.py` is the only glue: the network goes
through the entrance instead of Tor, work the core gives a thread runs inline,
and the vault is a file in memory.

`js/` holds a small JavaScript implementation of articles and ONNTP, used by
the tests and for a future verifier (UN-RFC 0043 section 7.4).

## License

Apache-2.0. `js/vendor/noble.js` bundles MIT-licensed code, see
[js/vendor/README.md](js/vendor/README.md).
