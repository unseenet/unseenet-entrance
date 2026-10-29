#!/bin/sh
# A web entrance (UN-RFC 0043) in a container (docs: "In Docker").
#
#   (no arguments)        the entrance: Tor with an onion address, the newest signed web
#                         client (fetched through the network, then updated by itself),
#                         and HTTPS as TLS says
#   sh, python3 ...       run that program instead
#
# TLS (as deploy/install.sh):
#   TLS=none (default)    plain HTTP on 8043 - your own proxy in front, or a LAN test
#   TLS=auto              a Let's Encrypt certificate for the public IP address
#                         (6-day certificates, renewed here); ports 80 and 443 published
#   TLS=domain:NAME       a normal certificate for NAME
#   TLS=both:NAME         one for NAME and the IP address; NAME sends visitors on to it
# Inside, the entrance listens on 8080 (HTTP) and 8443 (HTTPS): publish them as 80 and
# 443. Any ENTRANCE_* setting can be given in the environment (README.md).
# Nothing about visitors is written anywhere; everything kept is in /data.
set -eu
DATA=${UNSEENET_DATA:-/data}

if [ "$(id -u)" = 0 ]; then
    if [ "$(stat -c %u "$DATA")" != 10001 ]; then
        chown -R unseenet:unseenet "$DATA"
    fi
    chmod 700 "$DATA"
    exec su-exec unseenet "$0" "$@"
fi
case "${1:-}" in
    "") ;;
    *) exec "$@" ;;
esac
export HOME="$DATA"
say() { echo "unseenet-entrance: $*" >&2; }

# --- Tor: SOCKS for the tunnels, and the entrance as an onion service ---
mkdir -p "$DATA/tor/data" "$DATA/tor/onion"
chmod 700 "$DATA/tor" "$DATA/tor/data" "$DATA/tor/onion"
cat > "$DATA/tor/torrc" <<TORRC
SocksPort 127.0.0.1:9050
DataDirectory $DATA/tor/data
HiddenServiceDir $DATA/tor/onion
HiddenServicePort 80 127.0.0.1:8043
TORRC
tor -f "$DATA/tor/torrc" --quiet &
for i in $(seq 1 60); do [ -f "$DATA/tor/onion/hostname" ] && break; sleep 1; done

# --- settings: the environment wins, these are the defaults ---
export ENTRANCE_HOST="${ENTRANCE_HOST:-0.0.0.0}"
export ENTRANCE_SOCKS="${ENTRANCE_SOCKS:-127.0.0.1:9050}"
export ENTRANCE_NODES="${ENTRANCE_NODES:-known}"
export ENTRANCE_WEB_DIR="$DATA/web/current"
export ENTRANCE_RELEASES_DIR="$DATA/web"
export ENTRANCE_AUTO_UPDATE="${ENTRANCE_AUTO_UPDATE:-web}"
export ENTRANCE_MIRROR="${ENTRANCE_MIRROR:-off}"
export ENTRANCE_MIRROR_DIR="$DATA/mirror"
export ENTRANCE_ONION="$(cat "$DATA/tor/onion/hostname" 2>/dev/null || true)"
mkdir -p "$DATA/web" "$DATA/mirror"

# --- the web client: the newest signed version, through the network ---
if [ ! -e "$DATA/web/current" ]; then
    say "fetching the newest signed web client through the network (Tor can take minutes) ..."
    if ! python3 -m unseenet_entrance.release --install --wait "${ENTRANCE_WEB_WAIT:-600}"; then
        say "no signed web client found yet; trying again on the next start"
        exit 75                                    # temporary: a restart policy tries again
    fi
fi

# --- HTTPS ---
TLS=${TLS:-none}
LE="$DATA/letsencrypt"
public_ip() {
    [ -n "${ENTRANCE_PUBLIC_IP:-}" ] && { echo "$ENTRANCE_PUBLIC_IP"; return; }
    python3 - <<'PY'
import ipaddress, urllib.request
for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
    try:
        ip = urllib.request.urlopen(url, timeout=10).read().decode().strip()
        ipaddress.ip_address(ip)
        print(ip)
        break
    except Exception:
        pass
PY
}
certbot_() {
    /opt/certbot/bin/certbot --config-dir "$LE" --work-dir "$LE/work" --logs-dir "$LE/logs" "$@"
}
plain() {
    export ENTRANCE_PORT=8043 ENTRANCE_HTTP_PORT=0 ENTRANCE_ONION_PORT=0
    unset ENTRANCE_TLS_CERT ENTRANCE_TLS_KEY
}
with_tls() {
    export ENTRANCE_PORT=8443 ENTRANCE_HTTP_PORT=8080 ENTRANCE_ONION_PORT=8043
    export ENTRANCE_ACME_DIR="$DATA/acme"
    export ENTRANCE_TLS_CERT="$LE/live/unseenet-entrance/fullchain.pem"
    export ENTRANCE_TLS_KEY="$LE/live/unseenet-entrance/privkey.pem"
}
# Ask Let's Encrypt, with the entrance answering the challenge on 8080 meanwhile.
certificate() {
    mkdir -p "$DATA/acme" "$LE"
    [ -f "$LE/live/unseenet-entrance/fullchain.pem" ] && return 0
    ENTRANCE_PORT=8043 ENTRANCE_HTTP_PORT=8080 ENTRANCE_ONION_PORT=0 \
        ENTRANCE_ACME_DIR="$DATA/acme" python3 -m unseenet_entrance &
    challenge=$!
    sleep 3
    # no e-mail address: nothing ties this entrance to a person at Let's Encrypt
    # shellcheck disable=SC2086
    certbot_ certonly --non-interactive --agree-tos --register-unsafely-without-email \
        --webroot -w "$DATA/acme" --cert-name unseenet-entrance --reuse-key $1 && ok=0 || ok=1
    kill "$challenge" 2>/dev/null || true
    wait "$challenge" 2>/dev/null || true
    conf="$LE/renewal/unseenet-entrance.conf"     # 6-day certificates: renew 2 days before
    if [ $ok = 0 ] && [ "${TLS#domain:}" = "$TLS" ] && ! grep -q '^renew_before_expiry' "$conf"; then
        sed -i '1i renew_before_expiry = 2 days' "$conf"
    fi
    return $ok
}
case "$TLS" in
    none) plain ;;
    auto|ip)
        IP=$(public_ip)
        if [ -n "$IP" ] && certificate "--ip-address $IP --required-profile shortlived"; then
            with_tls; else
            say "no certificate for the public IP address ${IP:-(unknown)}: are ports 80 and 443"
            say "published and open from the internet? Plain HTTP on 8043 for now."; plain; fi ;;
    domain:*)
        if certificate "-d ${TLS#domain:}"; then with_tls; else
            say "no certificate for ${TLS#domain:}: does it point here?"; plain; fi ;;
    both:*)
        IP=$(public_ip)
        if [ -n "$IP" ] && certificate "-d ${TLS#both:} --ip-address $IP --required-profile shortlived"; then
            with_tls; export ENTRANCE_HOME="https://$IP"; else
            say "no certificate for ${TLS#both:} and ${IP:-(unknown)}"; plain; fi ;;
    *) say "unknown TLS=$TLS (none, auto, domain:NAME or both:NAME)"; exit 64 ;;
esac
if [ -n "${ENTRANCE_TLS_CERT:-}" ]; then
    # renew twice a day; 6-day certificates two days before they end. The entrance
    # loads a new certificate by itself.
    ( while sleep 43200; do
          certbot_ renew -q || true
      done ) &
fi

say "onion address: http://${ENTRANCE_ONION:-?}/"
echo "$ENTRANCE_PORT" > "$DATA/.port"           # for the health check
exec python3 -m unseenet_entrance
