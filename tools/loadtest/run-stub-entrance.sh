#!/bin/sh
# Start a second entrance (TLS, port 18443) next to the real one, and sample it.
# Run on the entrance machine; stop with: sh run-stub-entrance.sh stop
#   sh run-stub-entrance.sh stub [delay-seconds]   - against stubs.py (no Tor)
#   sh run-stub-entrance.sh tor                    - against the machine's Tor (9050)
# MAXSIZE=16384 shrinks asyncio's per-TLS-connection read buffer (default 256 KiB).
cd "$(dirname "$0")"
PY=${PY:-/opt/unseenet-entrance/.venv/bin/python}
MODE=${1:-stub}
for f in stubs.pid entrance.pid sample.pid; do
  [ -f $f ] && kill "$(cat $f)" 2>/dev/null; rm -f $f
done
[ "$MODE" = stop ] && exit 0
sleep 0.5
SOCKS=127.0.0.1:9050
if [ "$MODE" = stub ]; then
  prlimit --nofile=65536 $PY stubs.py --delay "${2:-0}" > stubs.log 2>&1 &
  echo $! > stubs.pid
  SOCKS=127.0.0.1:19050
  sleep 1
fi
ENTRANCE_HOST=0.0.0.0 ENTRANCE_PORT=18443 ENTRANCE_SOCKS=$SOCKS \
ENTRANCE_NODES=any ENTRANCE_MAX_TOTAL=1000000 ENTRANCE_MAX_PER_VISITOR=1000000 \
ENTRANCE_TLS_CERT=$PWD/cert.pem ENTRANCE_TLS_KEY=$PWD/key.pem ENTRANCE_AUTO_UPDATE=off \
MAXSIZE=${MAXSIZE:-262144} prlimit --nofile=65536 $PY -c "
import os, asyncio.sslproto as s
s.SSLProtocol.max_size = int(os.environ['MAXSIZE'])
from unseenet_entrance.server import main
main()" > entrance.log 2>&1 &
echo $! > entrance.pid
sleep 2
cat entrance.log
TOR=$(pgrep -x tor | head -1)
$PY sample.py entrance="$(cat entrance.pid)" tor="$TOR" --every 5 > sample.log 2>&1 &
echo $! > sample.pid
echo "mode=$MODE entrance=$(cat entrance.pid) tor=$TOR, sampling to sample.log"
