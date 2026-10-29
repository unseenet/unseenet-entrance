# Running an entrance

A **web entrance** ([UN-RFC 0043](https://unseenet.org/spec/un-rfc-0043/)) lets an ordinary
browser into Unseenet: anyone who opens its address gets the
[graphical client](https://unseenet.org/graphical/), running in their own browser, with their
keys kept there. The entrance serves the client and carries its connections to
the nodes over Tor. It cannot read, change or forge anything: it only moves
bytes.

Every entrance that someone else runs makes Unseenet harder to block, so this
is one of the most useful things you can do for the network besides
[running a node](https://unseenet.org/node/install/). The code is
[unseenet-entrance](https://github.com/unseenet/unseenet-entrance).

!!! warning "Pre-alpha"
    Neither the entrance nor the Unseenet core has had a security review.
    Read [Your responsibility](responsibility.md) before you open one to others.

## What an entrance does, and does not

- It tunnels a browser's WebSocket to a **known node's** onion service and
  copies bytes both ways, unchanged. One web-client session shares a Tor
  circuit to each node, as Unseenet's own clients do; other sessions and
  other visitors never share one.
- It serves the web client, and the signed lists it reads from the network
  about once an hour: the entrances and the versions of the web client, as
  raw signed articles that clients check themselves.
- It writes nothing about visitors anywhere: no access log, no error that
  names an address. Limits are counted in memory, under a key made at start.
- It does **not** hold anyone's keys or vault, sign anything, or see what is
  in a closed group or a private post.

## Installation

On Debian 12/13 or Ubuntu 24.04+ - a free cloud VM is enough (1 GB of memory
carries a few thousand visitors) - with ports 80 and 443 open to the internet:

```sh
curl -fsSLO https://raw.githubusercontent.com/unseenet/unseenet-entrance/main/deploy/install.sh
sudo sh install.sh
```

That installs the newest release of the entrance and Tor, gives the entrance
an onion address, installs the newest signed version of the web client
through the network, gets a Let's Encrypt certificate for the machine's
**public IP address** (no domain needed; the 6-day certificates renew by
themselves), and runs it as its own user with the journal in memory only. At
the end it prints both addresses.

The choices you can give on the command line - HTTPS, a release mirror, the
journal - are in [Settings](settings.md); certificates in more detail in
[Certificates](certificates.md). Run the same command again to update the
entrance's own code (`REF=vX.Y.Z` installs another release); your settings
are kept.

### In Docker

On any machine with Docker - not only Debian or Ubuntu - `ghcr.io/unseenet/entrance`
(amd64 and arm64, published for releases only, signed with cosign) does the same:

```sh
docker run -d --name entrance --restart unless-stopped -v entrance:/data \
    -p 80:8080 -p 443:8443 -e TLS=auto ghcr.io/unseenet/entrance
docker logs -f entrance        # the onion address, and where it got to
```

or with [`compose.yaml`](https://github.com/unseenet/unseenet-entrance/blob/main/compose.yaml)
(`docker compose up -d`), which also runs it with a read-only root file system
and no capabilities beyond handing `/data` to its own user. Inside, the
entrance listens on 8080 and 8443: publish them as 80 and 443.

- `TLS` is as for the install script: `auto` (the public IP address),
  `domain:NAME`, `both:NAME`, or `none` (plain HTTP on 8043, for your own proxy
  in front - publish 8043 then). Certificates are renewed in the container.
- Every setting of [Settings](settings.md) can be given in the environment
  (`-e ENTRANCE_OPERATOR=...`, `-e ENTRANCE_MIRROR=portable`).
- The volume keeps the web client, the onion address's keys and the
  certificates - nothing about visitors. The web client updates itself as
  on any entrance; a new image updates the entrance's own code
  (`docker pull`, then start it again).
- The first start fetches the signed web client through Tor, which can take
  a few minutes. If no node answers in time it stops with code 75 and a
  restart policy tries again.

## Being listed

Apps and the jump page find entrances in a signed list kept by the project
(`unseenet.entrances`, UN-RFC 0043 section 8.1). How to get yours on it, and
how it is checked every day: [Being listed](listed.md).
