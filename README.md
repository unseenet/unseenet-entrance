<p align="center"><img src="web/icon.svg" alt="" width="64"></p>

<h1 align="center">unseenet-entrance</h1>

<p align="center"><b>A web entrance to <a href="https://unseenet.org">Unseenet</a>: the graphical client in an ordinary browser, on a phone or a computer, with nothing to install.</b></p>

> **Pre-alpha.** Neither this nor the Unseenet core has had a security
> review. It is the less hidden way in, on purpose - see
> [What you give up](#what-you-give-up).

Unseenet is a discussion network over Tor: groups and nodes that nobody can
remove, and posts that nobody but their author can delete or change. Its
own clients start Tor themselves. A **web entrance**
([UN-RFC 0043](https://unseenet.org/spec/un-rfc-0043/)) is for everyone who
will not install anything: open its address in a browser, and you have the
same client, with your keys in your own browser.

This repository is two things:

- **The entrance** - a small server anyone can run on a free cloud VM. It
  serves the web client, and carries the client's connections to the nodes
  over Tor. It cannot read, change or forge anything: it only moves bytes.
- **The web client** - not a third client, but
  [unseenet-gui](https://github.com/unseenet/unseenet-gui) itself, with its
  backend (the real [unseenet](https://github.com/unseenet/unseenet) core)
  running in the browser. Every fix to the core or the GUI reaches the web too.

## Using it

Open an entrance's address, for example the project's own,
**https://entrance.unseenet.net/**. It sends you on to the entrance's IP
address, https://129.151.209.26/, which is where the app lives - and which
still works if the name is ever blocked. Create a vault - a name and a passphrase - or open one
you have. A bar at the top always says that you are on the open web.

- **Keep your vault.** Your keys live in the vault, encrypted with your
  passphrase. Either **Save vault file** after a session and open it next
  time, or choose **Keep on this device**: then the browser keeps it, still
  encrypted, and saves every change by itself.
- **Add it to the home screen** (Share → *Add to Home Screen* on an iPhone,
  ⋮ → *Install app* or *Add to home screen* on Android, the install icon in
  the address bar on a computer). It then starts like an app, from the
  device, and finds your vault at once.
- **When the entrance is gone.** The app keeps working: it talks through
  another entrance from the signed list. **Other entrances** in the bar saves
  the jump page, a small file that finds one that answers - keep it, send it
  to a friend. See [When an entrance is blocked](#when-an-entrance-is-blocked).
- **New versions** are offered in the bar (*New version*), and installed only
  when you say so, after every file has been checked against the project's
  signature. See [New versions](#new-versions).
- **In Tor Browser**, use the entrance's onion address: then the entrance
  does not see your IP address either.

On a phone the client shows one thing at a time: your groups, a group, a
post. The **⋯** button holds the rest.

**A new key ripens first.** Most nodes ask a key to have worked a while
before it posts (key ripening, UN-RFC 0025). The client does that work in
the browser, in the background, while the page is open. A brand-new key
takes 10-15 minutes before its first post goes out, on a computer or a
recent phone. After that the key keeps ripening while the page is open:
each level makes postage cheaper. Closing the page loses at most the step in
progress, because the key's progress is kept in your vault, and the same
vault ripens on in Unseenet's own client.

## What you give up

| | Unseenet's own client | Through an entrance |
|---|---|---|
| Groups and nodes can be removed | No | No |
| Others can delete or change your posts | No | No |
| Nodes learn your IP address | No | No |
| The entrance learns your IP address | - | **Yes** (not in Tor Browser) |
| You trust code that is served to you | Once, signed | Signed versions, kept on the device - but see below |
| Easy to block | Hard | This entrance, yes |

The app keeps itself on the device and runs only versions whose every file
matches a signed post. But the browser fetches the app's service worker
itself, from the entrance, and whoever controls the entrance could change it;
the app warns when it is not a signed one. If you need to hide that you use
Unseenet, or who you are, use the [real client](https://unseenet.org).

## Run an entrance

The network gets freer with every entrance that someone else runs. On
Debian 12/13 or Ubuntu 24.04+ - a free cloud VM is enough - with ports 80 and
443 open to the internet:

```sh
curl -fsSLO https://raw.githubusercontent.com/unseenet/unseenet-entrance/main/deploy/install.sh
sudo sh install.sh
```

That installs Tor, gives the entrance an onion address, installs the newest
signed version of the web client through the network, gets a Let's Encrypt
certificate for the machine's **public IP address** (no domain needed; the
6-day certificates renew by themselves), and runs it as its own user with
the journal in memory only. At the end it prints both addresses. Choices,
given before `sh`:

| Setting | |
|---|---|
| `TLS=auto` (default) | A certificate for the public IP address; plain HTTP on port 8043 if that fails, with the reason |
| `TLS=domain:NAME` | A normal certificate for NAME (point it here first) |
| `TLS=both:NAME` | One certificate for NAME and the public IP address. NAME is the one to share; it sends visitors on to the address, so the app and a vault kept on the device live at one place, which still works when the name is blocked |
| `TLS=none` | Plain HTTP on port 8043: a LAN test, or your own proxy in front |
| `MIRROR=portable` or `all` | Also mirror Unseenet's releases - see [A mirror](#a-mirror-of-the-releases) |
| `BUILD=local` | Build the web client here instead of installing the signed one (it says it is not signed) |
| `JOURNAL=keep` | Leave the system journal as it is, on a machine that does other things too |

Run the same command again to update the entrance's own code; settings in
`/etc/unseenet-entrance.env` are kept. Its other settings:

| In `/etc/unseenet-entrance.env` | |
|---|---|
| `ENTRANCE_OPERATOR`, `ENTRANCE_CONTACT` | Who runs it, shown in `/.well-known/unseenet-entrance` |
| `ENTRANCE_AUTO_UPDATE=off` | Stop the web client from updating itself |
| `ENTRANCE_NODES=any` | Tunnel to any onion node, not only the known ones |
| `ENTRANCE_MAX_PER_VISITOR`, `ENTRANCE_MAX_TOTAL` | Open tunnels per visitor (6) and in all (`auto`: what the available memory and the open-file limit allow, about 120 KB and two open files per tunnel; printed at start; a number overrides it) |
| `ENTRANCE_TRUST_FORWARDED=1` | A reverse proxy on this machine sits in front |

Then `systemctl restart unseenet-entrance`. Never put it behind a CDN that
ends TLS on someone else's machine (UN-RFC 0043 section 5). A TLS tunnel costs
the entrance about 100 KB of memory; for a very busy entrance, TLS ended by
Caddy or HAProxy **on the same machine** (`TLS=none`, the entrance on
127.0.0.1, `ENTRANCE_TRUST_FORWARDED=1`, and that proxy's logs off) brings it
to about 36 KB - allowed by section 5, but one more program to keep quiet. Certificates:
[docs/certificates.md](docs/certificates.md). To have your entrance in the
signed list that apps and the jump page use, open an issue here.

### What an entrance does, and does not

- It tunnels a browser's WebSocket to a **known node's** onion service, each
  on a Tor circuit of its own, and copies bytes both ways, unchanged.
- It serves the web client, and the signed lists it reads from the network
  about once an hour: the entrances (`/.well-known/unseenet-entrances.json`)
  and the versions of the web client (`/.well-known/unseenet-releases.json`),
  as raw signed articles that clients check themselves.
- It writes nothing about visitors anywhere: no access log, no error that
  names an address. Limits are counted in memory, under a key made at start.

## When an entrance is blocked

Every entrance serves the **jump page**, `/unseenet-entrances.html`: one small
file with the signed list of entrances inside. Opened from disk it checks the
list against the project key, shows which entrances answer, opens a random
one that does, and can fetch a newer list and save itself again. An installed
app does the same by itself: when its entrance does not answer, it talks
through another one from the list (UN-RFC 0043 section 8).

**The client in a file.** `python3 tools/build_web.py --file-from
https://an.entrance/` builds `web/unseenet-web.html`: the whole web client and
the signed list in one file of about 19 MB. Opened from disk it picks an
entrance that answers and uses it only as a tunnel; no page is served, so a
blocked address or a changed page does not matter. Chromium and Firefox.

## New versions

Every entrance serves the same build of the web client, built by
[this repository's CI](.github/workflows/web.yml) - every day, and when
something changed - and posted in the project channel `unseenet.releases`
with the SHA-256 of every file (UN-RFC 0043 sections 7.2-7.3):

- **The app** shows *New version* and updates only when you say so. It takes
  the files from any entrance that has that version and uses them only when
  every one matches the signed post. It never goes back to an older version.
- **The entrance** updates the web client it serves by itself, the same way -
  from the release here, or file by file from other entrances - without a
  restart. Its own code is never updated by itself.

Neither needs GitHub: the posts are read through the network, and the files
can come from any entrance. The releases are also here, as `web-<version>`,
with the list of checksums to compare by hand.

## A mirror of the releases

An entrance can also mirror Unseenet's own releases, so that the portable
clients and sticks can update over Tor when GitHub is blocked:
`MIRROR=portable` (the portable zips and the USB zip, about 300 MB per
channel) or `MIRROR=all` (also a stick's update and image, about 1.1 GB).
It follows `unseenet.releases`, fetches each new version from GitHub or
another mirror, checks every file against the signed checksums, keeps only
the newest, and serves it at `/mirror/<channel>/` on its onion address and
its open address. Clients find it in the signed list of entrances and use
nothing that does not match the signature, so a mirror needs no trust.
`MIRROR=off` removes it.

## How it works

```
browser ─ page (unseenet-gui, unchanged) ─ shim.js ─ worker: Python + unseenet core + GUI backend
                                                         │ WebSocket
                                                     entrance ── Tor ── node (.onion)
```

`tools/build_web.py` puts unseenet-gui's page, a pinned Pyodide
(sha256-checked) and a zip of the Python code into `web/`; at run time nothing
is loaded from anywhere else. The GUI's backend runs in
[Pyodide](https://pyodide.org) in a Web Worker; `web/shim.js` answers the
page's `/api/` calls from that worker. `web/py/webbackend.py` is the only
glue: the network goes through the entrance instead of Tor, work the core
gives a thread runs inline, and the vault is a file in memory. `web/sw.js`
keeps the client on the device and serves only what the page has checked.

## Develop

```sh
python3 -m venv .venv && .venv/bin/pip install -e .
python3 tools/build_web.py --core ../unseenet --gui ../unseenet-gui   # or without: clones from GitHub
ENTRANCE_SOCKS=127.0.0.1:9050 .venv/bin/unseenet-entrance               # needs a running Tor
# open http://127.0.0.1:8043/
```

`tests/lab_updates.py` runs two entrances on this machine with a test key and
signed test versions, without the network (see its docstring);
`python -m pytest tests` the Python tests.

```
src/unseenet_entrance/
  server.py        the entrance: tunnel, static files, the lists, headers
  listfetch.py     the signed lists, read from the nodes over Tor
  release.py       the web client updating itself; installing a signed version
  mirror.py        mirroring Unseenet's releases
web/               shim.js, sw.js, worker.js, py/ - and the build's output
js/                articles, signed lists and versions checked in JavaScript
jump/              the jump page
tools/             build_web.py, post_release.py (CI), chiavdf/ (key ripening for Pyodide)
deploy/install.sh  the installer
```

## Versions and releases

The entrance follows [semantic versioning](https://semver.org/) on the same
principles as [unseenet](https://github.com/unseenet/unseenet/blob/main/docs/development.md#versioning):
tags `vX.Y.Z` on `main`, the changes in [CHANGELOG.md](CHANGELOG.md), and
releases on [PyPI](https://pypi.org/p/unseenet-entrance). Each release names
the minor version of unseenet it works with (`unseenet>=0.4.1,<0.5`), and the
web client is built from the releases named in `.github/workflows/web.yml`
(`CORE_REF`, `GUI_REF`). `install.sh` installs the newest release tag.

The web client has versions of its own, `web-<version>`, made by `web.yml`
whenever a file changes and posted in `unseenet.releases`.

To release the entrance: move the entries under `## [Unreleased]` in
`CHANGELOG.md` to a new `## [X.Y.Z] - YYYY-MM-DD` section, set `__version__`
in `src/unseenet_entrance/__init__.py`, commit, then
`git tag -a vX.Y.Z -m "unseenet-entrance X.Y.Z" && git push origin vX.Y.Z`.

## License

Apache-2.0. `js/vendor/noble.js` bundles MIT-licensed code, see
[js/vendor/README.md](js/vendor/README.md).
