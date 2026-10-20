<p align="center"><img src="web/icon.svg" alt="" width="64"></p>

<h1 align="center">unseenet-entrance</h1>

<p align="center"><b>A web entrance to <a href="https://unseenet.org">Unseenet</a>: read, post and chat from an ordinary browser.</b></p>

> **Pre-alpha, first draft.** Neither this nor the Unseenet core has had a
> security review. It is the less hidden way in, on purpose - see below.

A **web entrance** ([UN-RFC 0043](https://unseenet.org/spec/un-rfc-0043/)) lets
someone use Unseenet without installing anything. This repository has two
parts:

- **The entrance** (`src/unseenet_entrance`) - a dumb tunnel. For each browser
  WebSocket it opens one connection over Tor to a node's onion service, on a
  Tor circuit of its own, and copies bytes both ways, unchanged. It only
  connects to known nodes, adds nothing, caches nothing and logs nothing.
- **The web client** (`web/`) - everything with keys happens in the browser:
  it makes your identity, checks every signature, signs your posts and pays
  their proof-of-work. It loads nothing from anywhere else.

## What you get - and what you give up

Groups and nodes still cannot be removed by anyone, and nobody can delete or
change someone else's signed post. But **the entrance sees your IP address**,
and you trust the code it serves on every visit. The page says so at all
times. If you need to hide that you use Unseenet, or who you are, use the
[real client](https://unseenet.org) instead.

## The first web client can

- connect through the entrance to a node you pick, over Tor
- list groups, show discussions as a thread tree and chat groups as a chat
- check every post's signature (✓ signed / ✗ does not verify)
- make a new identity in the browser, save and open it as a key file
- write new posts, reply with a quote, and send chat lines, with postage
  paid in a background worker

Not yet: vaults (UN-RFC 0007), closed and moderated groups, private posts,
the signed list of entrances, the pinned loader and the client in a file.

## Run an entrance

On Debian 12/13 or Ubuntu 24.04+ (a free cloud VM is enough):

```sh
curl -fsSLO https://raw.githubusercontent.com/unseenet/unseenet-entrance/main/deploy/install.sh
sudo sh install.sh
```

This installs Tor, gives the entrance an onion address, runs it as its own
user with a journal kept in memory only, and listens on port 8043. Settings
are in `/etc/unseenet-entrance.env`. For people on the open web you also need
HTTPS: see [docs/certificates.md](docs/certificates.md). Never put it behind a
CDN that ends TLS on someone else's machine (UN-RFC 0043 section 5).

## Develop

```sh
python3 -m venv .venv && .venv/bin/pip install -e .
ENTRANCE_SOCKS=127.0.0.1:9050 .venv/bin/unseenet-entrance     # needs a running Tor
# open http://127.0.0.1:8043/
node --experimental-default-type=module tests/proto.test.mjs some-article.eml
```

`tests/check_with_reference.py` checks an article made by the web client with
the reference implementation in [unseenet](https://github.com/unseenet/unseenet).

## License

Apache-2.0. `web/vendor/noble.js` bundles MIT-licensed code, see
[web/vendor/README.md](web/vendor/README.md).
