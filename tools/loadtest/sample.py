"""Print memory, CPU and open files of processes every few seconds.

    python sample.py NAME=PID [NAME=PID ...] [--every 2]

CPU is the percentage of one core since the last line.
"""
import argparse
import os
import time

TICK = os.sysconf("SC_CLK_TCK")


def read(pid: int) -> tuple[int, int, int]:
    with open(f"/proc/{pid}/stat") as f:
        fields = f.read().rsplit(")", 1)[1].split()
    cpu = int(fields[11]) + int(fields[12])
    with open(f"/proc/{pid}/status") as f:
        rss = next(int(x.split()[1]) for x in f if x.startswith("VmRSS"))
    fds = len(os.listdir(f"/proc/{pid}/fd"))
    return cpu, rss, fds


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("procs", nargs="+")
    p.add_argument("--every", type=float, default=2)
    a = p.parse_args()
    procs = [(n, int(pid)) for n, pid in (x.split("=") for x in a.procs)]
    last = {pid: read(pid)[0] for _, pid in procs}
    t_last, t0 = time.monotonic(), time.monotonic()
    while True:
        time.sleep(a.every)
        now = time.monotonic()
        out = [f"{now - t0:6.0f}s"]
        for name, pid in procs:
            try:
                cpu, rss, fds = read(pid)
            except FileNotFoundError:
                out.append(f"{name}: gone")
                continue
            pct = 100 * (cpu - last[pid]) / TICK / (now - t_last)
            last[pid] = cpu
            out.append(f"{name}: rss={rss / 1024:6.1f}MB cpu={pct:5.1f}% fds={fds}")
        t_last = now
        print("  ".join(out), flush=True)


if __name__ == "__main__":
    main()
