# Changelog

All notable changes to unseenet-entrance are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[semantic versioning](https://semver.org/) on the same principles as
[unseenet](https://github.com/unseenet/unseenet/blob/main/docs/development.md#versioning).
The web client itself has its own versions (`web-<version>`, signed and posted
in `unseenet.releases`); this file is about the entrance that serves it.

## [Unreleased]

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

### Changed

- Release articles go through the core's `Session` and never ripen in CI:
  they take over the key's published chain (#17).

[Unreleased]: https://github.com/unseenet/unseenet-entrance/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/unseenet/unseenet-entrance/releases/tag/v0.1.0
