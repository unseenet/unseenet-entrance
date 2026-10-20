#!/bin/sh
# Install a web entrance (UN-RFC 0043) on Debian 12/13 or Ubuntu 24.04+.
#
#   sudo sh install.sh [source]
#
# source: a git URL or a local checkout (default: the GitHub repository).
# Sets up Tor (SOCKS for the tunnels, plus an onion address for the entrance
# itself), the entrance in a virtualenv as its own user, and a systemd unit
# that keeps nothing on disk about visitors: no access log, a journal kept in
# memory only. TLS is not set up here: see docs/certificates.md.
set -eu

SRC="${1:-https://github.com/unseenet/unseenet-entrance.git}"
APP=/opt/unseenet-entrance
ENVFILE=/etc/unseenet-entrance.env
PORT="${ENTRANCE_PORT:-8043}"

[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }

echo "== packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q tor python3-venv git ca-certificates >/dev/null

echo "== user and code"
id unseenet-entrance >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin unseenet-entrance
rm -rf "$APP.new"
if [ -d "$SRC" ]; then cp -a "$SRC" "$APP.new"; else git clone -q --depth 1 "$SRC" "$APP.new"; fi
rm -rf "$APP.new/.venv" "$APP.new/.git"
python3 -m venv "$APP.new/.venv"
"$APP.new/.venv/bin/pip" install -q --upgrade pip
"$APP.new/.venv/bin/pip" install -q "$APP.new"
rm -rf "$APP.old"; [ -d "$APP" ] && mv "$APP" "$APP.old"; mv "$APP.new" "$APP"
chown -R root:root "$APP"

echo "== tor"
mkdir -p /etc/tor/torrc.d
cat > /etc/tor/torrc.d/unseenet-entrance.conf <<EOF
# unseenet-entrance: SOCKS for the tunnels (one circuit per credentials, the
# default IsolateSOCKSAuth), and the entrance as an onion service.
SocksPort 127.0.0.1:9050
HiddenServiceDir /var/lib/tor/unseenet-entrance/
HiddenServicePort 80 127.0.0.1:$PORT
EOF
grep -q '^%include /etc/tor/torrc.d' /etc/tor/torrc || echo '%include /etc/tor/torrc.d/*.conf' >> /etc/tor/torrc
# Tor: notices to the (in-memory) journal only; it never logs client addresses by default.
systemctl enable -q tor
systemctl restart tor
for i in $(seq 1 60); do [ -f /var/lib/tor/unseenet-entrance/hostname ] && break; sleep 1; done
ONION=$(cat /var/lib/tor/unseenet-entrance/hostname 2>/dev/null || echo "")

echo "== journal in memory only"
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=16M\n' > /etc/systemd/journald.conf.d/unseenet-entrance.conf
systemctl restart systemd-journald

echo "== settings ($ENVFILE)"
if [ ! -f "$ENVFILE" ]; then
  cat > "$ENVFILE" <<EOF
# unseenet-entrance settings. See README.md.
ENTRANCE_HOST=0.0.0.0
ENTRANCE_PORT=$PORT
ENTRANCE_SOCKS=127.0.0.1:9050
ENTRANCE_WEB_DIR=$APP/web
ENTRANCE_NODES=known
ENTRANCE_ONION=$ONION
ENTRANCE_OPERATOR=
ENTRANCE_CONTACT=
# Set to 1 only when a reverse proxy or tunnel on this machine sits in front.
ENTRANCE_TRUST_FORWARDED=0
EOF
else
  sed -i "s|^ENTRANCE_ONION=.*|ENTRANCE_ONION=$ONION|" "$ENVFILE"
fi

echo "== service"
cat > /etc/systemd/system/unseenet-entrance.service <<EOF
[Unit]
Description=Unseenet web entrance (UN-RFC 0043)
After=network-online.target tor.service
Wants=network-online.target

[Service]
User=unseenet-entrance
EnvironmentFile=$ENVFILE
ExecStart=$APP/.venv/bin/python -m unseenet_entrance
Restart=always
RestartSec=3
# nothing about visitors is written anywhere
StandardOutput=journal
StandardError=journal
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
NoNewPrivileges=yes
ReadOnlyPaths=/
LimitCORE=0

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable -q unseenet-entrance
systemctl restart unseenet-entrance
sleep 2
systemctl --no-pager --lines=3 status unseenet-entrance | head -5

echo
echo "Web entrance running on port $PORT."
[ -n "$ONION" ] && echo "Onion address: http://$ONION/"
echo "Settings: $ENVFILE   (then: systemctl restart unseenet-entrance)"
