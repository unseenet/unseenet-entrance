# Load test

How many web clients can one entrance carry? These scripts measure it in two
steps, so that most of the load never touches the real Tor network:

1. **The entrance on its own.** `stubs.py` stands in for Tor (a SOCKS5 server)
   and for a node, so thousands of tunnels cost nothing but the entrance's own
   memory and CPU.
2. **What Tor adds.** The same client against a real test node over the real Tor
   network, with small numbers only (a few hundred circuits).

| Script | Runs on | What |
|---|---|---|
| `stubs.py` | the entrance machine | stub SOCKS5 (port 19050) and stub node (11900) |
| `run-stub-entrance.sh stub\|tor\|stop` | the entrance machine | a second entrance with TLS on port 18443, plus `sample.py` |
| `sample.py NAME=PID …` | any machine | RSS, CPU and open files of processes every few seconds |
| `load.py wss://HOST:18443 …` | another machine | opens web-client sessions and times them |

A session is what the web client keeps open: one connection per node it uses
and one watch connection per node (the core's `Session` and the GUI's
`_start_watches`). `load.py` opens two tunnels per session.

```sh
# on the entrance machine (here LXC 168: 1 core, 512 MB)
sh run-stub-entrance.sh stub                    # or: MAXSIZE=16384 sh run-stub-entrance.sh stub
# elsewhere
ulimit -n 65536
python load.py wss://192.168.65.43:18443 --sessions 1500 --rate 50 --hold 60
# real Tor, a test node only, small numbers
sh run-stub-entrance.sh tor                     # on the entrance machine
python load.py wss://192.168.65.43:18443 --mode real --node <test node onion> \
    --sessions 100 --rate 2 --hold 90 --interval 30
sh run-stub-entrance.sh stop
```

Never point `--mode real` at a production node, and keep the numbers small:
every tunnel is an onion circuit on the volunteer-run Tor network.

## Results, 21 October 2026

unseenet-entrance 0.1.0, Python 3.13 (LXC 168) and 3.14 (Fedora, one core with
`taskset`), aiohttp 3.13.

### The entrance without Tor

Memory per open tunnel (1000 tunnels, each main tunnel fetching 20 KB every 5 s):

| | per tunnel | 1000 tunnels |
|---|---|---|
| plain WebSocket (no TLS) | ~36 KB | 77 MB |
| TLS, as today | **~333 KB** | 374 MB |
| TLS, asyncio read buffer 64 KiB | ~146 KB | 187 MB |
| TLS, asyncio read buffer 16 KiB | ~99 KB | 140 MB |

Python's `asyncio.sslproto.SSLProtocol` gives every TLS connection a
`bytearray(256 * 1024)` read buffer (`max_size`). That is most of the 333 KB.

On LXC 168 (1 core, 512 MB):

- as today, the entrance was **killed by the OOM killer at about 1650 tunnels**
  (≈ 800 sessions), at 360 MB;
- with the 16 KiB buffer it held **3000 tunnels (1500 sessions) at 285 MB**;
- CPU was 2–3 % of one core with 3000 tunnels open and requests flowing, about
  13 % while 100 new tunnels a second were opened;
- memory is not handed back when tunnels close (300 MB stays used), but it is
  reused for the next ones.

Limits that are hit before memory:

- `ENTRANCE_MAX_TOTAL=300` (default): 150 sessions;
- the systemd unit has no `LimitNOFILE`, so the soft limit is **1024 open files**;
  each tunnel takes two (WebSocket and SOCKS), so about **500 tunnels, 250
  sessions**.

### What Tor adds (test entrance LXC 168 → test node1 LXC 160, both 1 core)

| sessions | tunnels | connect p50 | p95 | max | failed |
|---|---|---|---|---|---|
| 25 | 50 | 2.3 s | 7.3 s | 10.9 s | 0 |
| 50 | 100 | 2.5 s | 7.3 s | 26.7 s | 0 |
| 100 | 200 | 2.5 s | 6.7 s | 10.9 s | 0 |

Round trip for a short command once connected: p50 ≈ 350 ms, p95 ≈ 0.9 s.

- **Opening** tunnels costs Tor CPU; **holding** them costs almost nothing. At
  4 new tunnels a second, the entrance's tor used ~10 % of a core and the node's
  tor ~5 %; with 200 tunnels open and idle, both were at 0–0.4 %. That is roughly
  40 new circuits a second per core at the entrance and 80 at the node, on this
  (fast) host CPU; a small cloud VM is slower.
- Memory: the entrance's tor grew ~12 MB for 200 circuits, the node's tor
  ~1 MB, the node itself ~2 MB (about 10 KB per connection).
- So Tor matters when many tunnels open at once - an entrance restart, or every
  client reconnecting after a network blip - not for a steady crowd.

### The first visit

The web client the entrance serves is 15 MB (Pyodide is most of it), 7.8 MB with
gzip. The entrance serves it uncompressed today. After the first visit the
service worker keeps it on the device, and `If-None-Match` revalidation answers
304, so the cost is once per new visitor (and once per new version).
