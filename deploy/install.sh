#!/bin/sh
# Install a web entrance (UN-RFC 0043) on Debian 12/13 or Ubuntu 24.04+.
#
#   sudo sh install.sh [source]
#
# source: a git URL or a local checkout (default: the GitHub repository).
#
# HTTPS, chosen with TLS=... (default auto):
#   TLS=auto            a Let's Encrypt certificate for this machine's public IP
#                       address (6-day certificates, renewed automatically); if
#                       that fails, plain HTTP on port 8043 and an explanation
#   TLS=domain:NAME     a normal Let's Encrypt certificate for NAME (point NAME here first)
#   TLS=none            plain HTTP on port 8043 (a LAN test, or your own proxy in front)
# Ports 80 and 443 must be open to the internet for the first two.
#
# Also sets up Tor (SOCKS for the tunnels and an onion address for the entrance),
# builds the web client (unseenet-gui in the browser), and runs the entrance as
# its own user with a journal kept in memory only: nothing about visitors is
# written to disk.
set -eu

SRC="${1:-https://github.com/unseenet/unseenet-entrance.git}"
TLS="${TLS:-auto}"
APP=/opt/unseenet-entrance
ENVFILE=/etc/unseenet-entrance.env
TLSDIR=/etc/unseenet-entrance/tls
ACME=/var/lib/unseenet-entrance/acme
CERTBOT=/opt/certbot/bin/certbot

[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }

echo "== packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q tor python3-venv git ca-certificates >/dev/null

echo "== user, code and the web client"
id unseenet-entrance >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin unseenet-entrance
rm -rf "$APP.new"
if [ -d "$SRC" ]; then cp -a "$SRC" "$APP.new"; else git clone -q --depth 1 "$SRC" "$APP.new"; fi
rm -rf "$APP.new/.venv" "$APP.new/.git"
python3 -m venv "$APP.new/.venv"
"$APP.new/.venv/bin/pip" install -q --upgrade pip
"$APP.new/.venv/bin/pip" install -q "$APP.new"
# LIST_OWNER / LIST_GROUP: test only - trust a lab list of entrances instead of the project's
if [ -n "${LIST_OWNER:-}" ] && [ -n "${LIST_GROUP:-}" ]; then
  python3 "$APP.new/tools/build_web.py" --list-owner "$LIST_OWNER" --list-group "$LIST_GROUP"
else
  python3 "$APP.new/tools/build_web.py"
fi
rm -rf "$APP.old"; [ -d "$APP" ] && mv "$APP" "$APP.old"; mv "$APP.new" "$APP"
chown -R root:root "$APP"

echo "== tor"
mkdir -p /etc/tor/torrc.d
cat > /etc/tor/torrc.d/unseenet-entrance.conf <<EOF
# unseenet-entrance: SOCKS for the tunnels (one circuit per credentials, the
# default IsolateSOCKSAuth), and the entrance as an onion service on port 80.
SocksPort 127.0.0.1:9050
HiddenServiceDir /var/lib/tor/unseenet-entrance/
HiddenServicePort 80 127.0.0.1:8043
EOF
grep -q '^%include /etc/tor/torrc.d' /etc/tor/torrc || echo '%include /etc/tor/torrc.d/*.conf' >> /etc/tor/torrc
systemctl enable -q tor
systemctl restart tor
for i in $(seq 1 60); do [ -f /var/lib/tor/unseenet-entrance/hostname ] && break; sleep 1; done
ONION=$(cat /var/lib/tor/unseenet-entrance/hostname 2>/dev/null || echo "")

echo "== journal in memory only"
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=16M\n' > /etc/systemd/journald.conf.d/unseenet-entrance.conf
systemctl restart systemd-journald

# setting NAME VALUE: set or add a line in the settings file
setting() {
  if grep -q "^$1=" "$ENVFILE"; then sed -i "s|^$1=.*|$1=$2|" "$ENVFILE"; else echo "$1=$2" >> "$ENVFILE"; fi
}

echo "== settings ($ENVFILE)"
if [ ! -f "$ENVFILE" ]; then
  cat > "$ENVFILE" <<EOF
# unseenet-entrance settings. See README.md.
ENTRANCE_HOST=0.0.0.0
ENTRANCE_SOCKS=127.0.0.1:9050
ENTRANCE_NODES=known
ENTRANCE_OPERATOR=
ENTRANCE_CONTACT=
# Set to 1 only when a reverse proxy or tunnel on this machine sits in front.
ENTRANCE_TRUST_FORWARDED=0
EOF
fi
setting ENTRANCE_WEB_DIR "$APP/web"
if [ -n "${LIST_GROUP:-}" ]; then setting ENTRANCE_LIST_GROUP "$LIST_GROUP"; fi
setting ENTRANCE_ONION "$ONION"

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
# ports 80 and 443 without running as root
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
# nothing about visitors is written anywhere
StandardOutput=journal
StandardError=journal
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
NoNewPrivileges=yes
LimitCORE=0

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable -q unseenet-entrance

plain_http() {
  setting ENTRANCE_PORT 8043
  setting ENTRANCE_HTTP_PORT 0
  setting ENTRANCE_ONION_PORT 0
  sed -i '/^ENTRANCE_TLS_/d' "$ENVFILE"
  systemctl restart unseenet-entrance
}

# Ask Let's Encrypt for a certificate. $1: "ip" or "domain", $2: the address or name.
get_certificate() {
  echo "== certbot"
  [ -x "$CERTBOT" ] || { python3 -m venv /opt/certbot && /opt/certbot/bin/pip install -q "certbot>=5.6"; }
  mkdir -p "$ACME" "$TLSDIR"
  # serve the challenges on port 80 while certbot works
  setting ENTRANCE_PORT 8043
  setting ENTRANCE_HTTP_PORT 80
  setting ENTRANCE_ONION_PORT 0
  setting ENTRANCE_ACME_DIR "$ACME"
  sed -i '/^ENTRANCE_TLS_/d' "$ENVFILE"
  systemctl restart unseenet-entrance
  sleep 2
  cat > /usr/local/sbin/unseenet-entrance-cert-deploy <<EOF
#!/bin/sh
# certbot deploy hook: hand the new certificate to the entrance (it reloads it by itself)
install -o root -g unseenet-entrance -m 0640 /etc/letsencrypt/live/unseenet-entrance/fullchain.pem $TLSDIR/fullchain.pem
install -o root -g unseenet-entrance -m 0640 /etc/letsencrypt/live/unseenet-entrance/privkey.pem $TLSDIR/privkey.pem
EOF
  chmod 755 /usr/local/sbin/unseenet-entrance-cert-deploy
  if [ "$1" = ip ]; then ident="--ip-address $2 --required-profile shortlived"; else ident="-d $2"; fi
  # no e-mail address: nothing ties this entrance to a person at Let's Encrypt
  # shellcheck disable=SC2086
  if "$CERTBOT" certonly --non-interactive --agree-tos --register-unsafely-without-email \
      --webroot -w "$ACME" --cert-name unseenet-entrance $ident --reuse-key \
      --deploy-hook /usr/local/sbin/unseenet-entrance-cert-deploy; then
    # 6-day certificates: renew two days before they run out (about twice a week)
    conf=/etc/letsencrypt/renewal/unseenet-entrance.conf
    if [ "$1" = ip ] && ! grep -q '^renew_before_expiry' "$conf"; then
      sed -i '1i renew_before_expiry = 2 days' "$conf"
    fi
    cat > /etc/systemd/system/unseenet-entrance-renew.service <<EOF
[Unit]
Description=Renew the web entrance's certificate
[Service]
Type=oneshot
ExecStart=$CERTBOT renew -q
EOF
    cat > /etc/systemd/system/unseenet-entrance-renew.timer <<EOF
[Unit]
Description=Renew the web entrance's certificate twice a day
[Timer]
OnCalendar=*-*-* 03,15:17
RandomizedDelaySec=1h
Persistent=true
[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload
    systemctl enable -q --now unseenet-entrance-renew.timer
    setting ENTRANCE_PORT 443
    setting ENTRANCE_ONION_PORT 8043
    setting ENTRANCE_TLS_CERT "$TLSDIR/fullchain.pem"
    setting ENTRANCE_TLS_KEY "$TLSDIR/privkey.pem"
    systemctl restart unseenet-entrance
    return 0
  fi
  return 1
}

public_ip() {
  [ -n "${ENTRANCE_PUBLIC_IP:-}" ] && { echo "$ENTRANCE_PUBLIC_IP"; return; }
  python3 - <<'EOF'
import ipaddress, urllib.request
for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
    try:
        ip = urllib.request.urlopen(url, timeout=10).read().decode().strip()
        ipaddress.ip_address(ip)
        print(ip)
        break
    except Exception:
        pass
EOF
}

URL=""
case "$TLS" in
  none)
    plain_http ;;
  domain:*)
    NAME="${TLS#domain:}"
    if get_certificate domain "$NAME"; then URL="https://$NAME/"; else
      echo "!! no certificate for $NAME: does it point here, and are ports 80 and 443 open?"; plain_http; fi ;;
  auto|ip)
    IP=$(public_ip)
    if [ -n "$IP" ] && get_certificate ip "$IP"; then URL="https://$IP/"; else
      echo "!! no certificate for the public IP address ${IP:-(unknown)}."
      echo "   Is this machine reachable from the internet on ports 80 and 443?"
      echo "   (At home: forward them in your router. In a cloud: open them in its firewall.)"
      echo "   Running plain HTTP on port 8043 for now; run this script again when it is."
      plain_http
    fi ;;
  *)
    echo "unknown TLS=$TLS"; exit 1 ;;
esac

sleep 2
systemctl --no-pager --lines=3 status unseenet-entrance | head -5
echo
if [ -n "$URL" ]; then echo "Web entrance: $URL"; else echo "Web entrance: http://<this machine>:8043/ (no HTTPS)"; fi
[ -n "$ONION" ] && echo "Onion address: http://$ONION/"
echo "Settings: $ENVFILE   (then: systemctl restart unseenet-entrance)"
