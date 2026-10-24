# Changelog

All notable changes to unseenet-entrance are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[semantic versioning](https://semver.org/) on the same principles as
[unseenet](https://github.com/unseenet/unseenet/blob/main/docs/development.md#versioning).
The web client itself has its own versions (`web-<version>`, signed and posted
in `unseenet.releases`); this file is about the entrance that serves it.

## [Unreleased]

### Added

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

[Unreleased]: https://github.com/unseenet/unseenet-entrance/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/unseenet/unseenet-entrance/releases/tag/v0.1.0
