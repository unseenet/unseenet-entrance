# Changelog

All notable changes to unseenet-entrance are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[semantic versioning](https://semver.org/) on the same principles as
[unseenet](https://github.com/unseenet/unseenet/blob/main/docs/development.md#versioning).
The web client itself has its own versions (`web-<version>`, signed and posted
in `unseenet.releases`); this file is about the entrance that serves it.

## [Unreleased]

- **The web client is built from core 0.6.2**: a key copied into a browser brings its
  ripening and shows its level at once (UN-RFC 0050 3.6 as amended).

## [0.3.1] - 2026-10-29

### Fixed

- **Copying and pictures work in the browser** (unseenet-gui#234): the web client lacked
  the core's `spake2` (copying an identity, UN-RFC 0050) and Pillow (pictures on posts),
  so both failed with `ModuleNotFoundError`. `spake2` is bundled; `cryptography` and
  `pillow` come from Pyodide. The build now stops when the core needs something the page
  does not carry (`CORE_DEPENDENCIES` in `tools/build_web.py`).
- **A copy waits for the other device** (#58): the lookup uses the code up, and the
  other device's one-time onion service can take a minute to be found; `/copy` gave it
  7 seconds and now about 90.

## [0.3.0] - 2026-10-29

**On unseenet 0.6 (`unseenet>=0.6,<0.7`), and a web client built from core 0.6.1 and
unseenet-gui 0.3.0.** A container image for running an entrance anywhere, a phone can get
its identity from another device through the entrance, and - most important for
operators already running one - entrances find and install the signed web client again.

- **A container image** (#47, unseenet/unseenet#571): `ghcr.io/unseenet/entrance` - the
  entrance, Tor with an onion address and certbot on the node image; the signed web
  client fetched through the network on the first start, and `TLS=auto`,
  `domain:NAME`, `both:NAME` or `none` as for the install script, with renewal in the
  container. Any `ENTRANCE_*` setting from the environment; everything kept in
  `/data`, nothing about visitors. A `compose.yaml` (ports 80 and 443, read-only root).
  Published for amd64 and arm64 only for release tags. See *In Docker* in the docs.
- **A browser can receive a copy of an identity** (#53, UN-RFC 0043 section 4.5):
  `/copy?onion=<v3>&session=<tag>` relays the one connection a browser client needs to get
  an identity from another device - to that device's one-time onion address on port 9119
  (UN-RFC 0050 section 3.9), nothing else; at most one per session at a time, counted in
  its limits, closed after ten minutes and 2 MiB each way. The entrance sees SPAKE2
  messages and ciphertext only. On by default, `ENTRANCE_COPY=off` to stop it, and
  `/.well-known/unseenet-entrance` says `"copy": true` or `false`, so clients only offer it
  where it works. The web client's transport (`web/py/webtransport.py`) sends the copy
  there instead of to Tor.

### Fixed

- **Entrances found no signed web client** (found while building the container image):
  `unseenet.releases` also carries the portable and bootable builds (unseenet#264),
  posted every night, and the entrance read only the newest five posts - all builds -
  so the web client's posts, days older, were never seen: a new entrance could not
  install the web client, and running ones did not update. It now looks at up to 400
  posts by their headers and keeps the newest five web client posts, which is also what
  `/.well-known/unseenet-releases.json` gives browsers.

- **The offline file carries every GUI script** (#50): `unseenet-web.html`
  inlined only `app.js`, so what lives in `identity.js`, `notify.js`, `publish.js`
  and `export.js` was missing there; now every `<script src="/static/...">` of
  the GUI's `index.html` is inlined, in its order.

## [0.2.0] - 2026-10-25

For unseenet 0.5 (`unseenet>=0.5,<0.6`); update the nodes first. One Tor circuit and limits per session (UN-RFC 0043 4.1.1, 4.2), the browser's own Tor used when it has one, the web client served compressed and ripening its own keys, and *Add my entrance*.

### Fixed

- **Updating the web client and the mirror from GitHub failed**: GitHub's
  download answers carry a header longer than aiohttp's limit of 8190 octets
  ("Got more than 8190 bytes when reading"), so the release zip could never be
  fetched. The limits are now 64 KiB. Found on the project's entrance on 24 Oct.

### Added

- `ENTRANCE_CLAIM`: while asking to be listed ("Add my entrance", #3), the
  challenge the request posts, shown in `/.well-known/unseenet-entrance` as
  `claim`, so the check sees that whoever asked runs the entrance.
- **Tor in the browser is used by itself** (#1): when .onion addresses work in
  the browser (Tor Browser, Onion Browser, Brave's Tor windows), the web client
  moves its tunnel to the entrance's own onion address, from
  `/.well-known/unseenet-entrance`, so the entrance cannot see the visitor's
  address. The page and the vault stay where they are; the bar turns purple,
  "Through Tor - the entrance cannot see your address". It asks again when the
  network comes back, and for another entrance chosen. In an ordinary browser
  nothing changes: the probe fails at once. Straight to a node's onion waits
  for nodes that take WebSockets (unseenet/unseenet#259).
- **One Tor circuit per session** (#21, UN-RFC 0043 4.1.1): the web client
  sends a random session tag, made when it starts and kept in memory only, and
  the entrance derives the SOCKS credentials from an HMAC of the visitor's
  address and the tag. A session's tunnels to one node share a circuit, so
  the entrance and the node build half as many; other sessions and other
  visitors never share one. Without a tag, a tunnel gets a circuit of its own.
- **Limits counted in sessions** (UN-RFC 0043 4.2):
  `ENTRANCE_MAX_SESSIONS_PER_VISITOR` (16), so people behind one address are
  no longer refused at 6 tunnels; `ENTRANCE_MAX_PER_VISITOR` is now a cap of
  64 tunnels. An entrance with `ENTRANCE_MAX_PER_VISITOR=6` set by hand keeps
  that cap - remove it.
- Visitors through the entrance's onion service, who all arrive from Tor on
  127.0.0.1, no longer share one visitor's limits: there each session counts as
  a visitor of its own (only the total bounds them, as nothing else can tell
  them apart).
- **The web client is served compressed** (#22): a `.gz` copy (and `.br` when
  the `brotli` module is installed) beside every file over 1 KB that gets at
  least 10 % smaller, made from files just checked against a signed release
  (or just built) and served by aiohttp to browsers that accept it. A first
  visit goes from about 15 MB to about 7.8 MB. Not in `release.sums`: the
  browser decompresses before the service worker checks a file.
- `tools/chiavdf`: chiavdf for Pyodide, so the web client can ripen its own
  keys (UN-RFC 0025). The wheel is built with GMP's limbs made 64-bit for
  wasm32, and checked in Pyodide against native chiavdf's proofs and the
  core's ripening self-check. The `chiavdf` workflow builds it and can
  release it (#27).
- **The web client ripens its keys** (UN-RFC 0025), so a new web user can
  post on nodes that ask for ripening. Segments are proved in a ripening
  worker of its own, with Pyodide and chiavdf, so the rest of the client
  runs on meanwhile. Stopping is terminating that worker. Progress is kept
  in the vault. Needs unseenet 0.4.2 (`Session._prove_segment`); with an
  older core, the web client doesn't ripen, as before (#27).

### Changed

- A TLS connection's read buffer is 16 KiB instead of asyncio's 256 KiB: a
  tunnel costs about 99 KB instead of 333 KB, so the same machine holds
  about three times as many visitors (#19).
- The web client is built from unseenet 0.4.2, so it ripens keys, and a
  new user reads at once while the key ripens.
- The web client is built from unseenet-gui 0.1.1: text you type in the
  compose window and on first start no longer disappears when the screen
  is redrawn.
- The page offers to save the vault when something in it changed or a key
  reached a new level ("Save vault file (key level 1)"), not after every
  ripening checkpoint, which came every few minutes. Leaving the page still
  warns while ripening steps are unsaved (#32).

## [0.1.0] - 2026-10-22

The first release of the web entrance (UN-RFC 0043). Works with unseenet 0.4
(`unseenet>=0.4.1,<0.5`); the web client is built from unseenet 0.4.1 and
unseenet-gui 0.1.0. The browser client can read but not yet post on 0.4
nodes (unseenet-gui#31).

### Added

- **The entrance:** lets a browser in over Tor, one circuit per session,
  with the web client being unseenet-gui running in the browser (Pyodide)
  on the core's local client API; keys and the vault stay in the browser.
- `deploy/install.sh` for Debian and Ubuntu: Tor, its own user, a journal
  kept in memory only, and HTTPS chosen with `TLS=`: a Let's Encrypt
  certificate for the public IP address by default, a name, both, or none.
  It shares a machine politely (reloads a running Tor, `JOURNAL=keep`) and
  installs the newest release (`REF=` picks another).
- **When an entrance is blocked:** the signed list of entrances, the jump
  page, the client in a single file (`unseenet-web.html`) that runs from
  disk, and the app switching to another entrance by itself; *Other
  entrances* to pick one at once.
- **An app for the home screen**, with the vault kept on the device if you
  want.
- **Signed updates of the web client** through the network: each build is
  released as `web-<version>` and its file list posted in
  `unseenet.releases`; entrances and apps check every file against the post.
- **A mirror of Unseenet's releases** for those who cannot reach GitHub.
- `ENTRANCE_HOME`: a name that sends visitors on to the entrance's address.
- `tools/loadtest`: how many web clients an entrance carries.
- `ENTRANCE_MAX_TOTAL=auto`, the default: as many tunnels as the machine's
  available memory and open-file limit allow, printed at start, and
  `LimitNOFILE=65536` in the unit `install.sh` writes; it was 300 tunnels
  (150 visitors) and 1024 open files whatever the machine (#20).

### Changed

- Release articles go through the core's `Session` and never ripen in CI:
  they take over the key's published chain (#17).

[Unreleased]: https://github.com/unseenet/unseenet-entrance/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/unseenet/unseenet-entrance/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/unseenet/unseenet-entrance/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/unseenet/unseenet-entrance/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/unseenet/unseenet-entrance/releases/tag/v0.1.0
