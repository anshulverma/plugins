#!/usr/bin/env python3
"""Fleet daemon - RUN THIS IN YOUR LOGIN SHELL (it needs your SSH cert; the agent
sandbox cannot SSH). One thread per host: the master runs units locally on its
8 H100s, the ODs run via SSH. Capability-aware (ODs=cpu only, master=cpu+gpu).
Stops when the queue drains (K empty rounds) or ~/.tfh/STOP appears.

Usage: serve_fleet.py <db> <hosts.txt> [base_commit]
Run from the fbcode dir so buck2 resolves targets.
"""
from __future__ import annotations

import csv
import os
import socket
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import dispatcher  # noqa: E402
import transport  # noqa: E402

TFH = os.path.expanduser("~/.tfh")
STOP = os.path.join(TFH, "STOP")


def _hosts(path):
    """host,role,capabilities,gpu_count[,slots]  (slots = concurrent loops, default 1)."""
    out = []
    for row in csv.reader(open(path)):  # csv handles quoted "cpu,gpu" correctly
        if not row or row[0].strip().startswith("#"):
            continue
        parts = [c.strip() for c in row]
        host, role, caps = parts[0], parts[1], parts[2]
        slots = int(parts[4]) if len(parts) > 4 and parts[4] else 1
        lane = parts[5] if len(parts) > 5 and parts[5] else "any"
        out.append((host, role, caps, slots, lane))
    return out


_LANE_GPU_REQS = {"cpu": {"cpu"}, "gpu": {"single", "multi"}, "any": None}


def _worker_loop(host, role, caps, db, base, is_master, gpu_reqs=None):
    conn = dispatcher.connect(db)
    tp = transport.local_transport if is_master else transport.ssh_transport(host)

    def transport_fn(env, unit):
        return (transport.local_transport(env, unit, tfh=TFH) if is_master else tp(env, unit))

    # Persistent: keep polling until an explicit ~/.tfh/STOP. When the queue is
    # empty we idle-poll (not exit), so the worker stays up across drains,
    # requeues, and phase transitions - no manual relaunch needed.
    while not os.path.exists(STOP):
        try:
            h = transport.serve_once_for_host(conn, host, caps, transport_fn, base, 1800,
                                              tfh=TFH, gpu_reqs=gpu_reqs)
        except Exception as e:  # a worker error should not kill the fleet
            print(f"[{host}] error: {e}", flush=True)
            time.sleep(10)
            continue
        if h is None:
            time.sleep(10)  # idle: nothing eligible right now, keep polling
        else:
            st = conn.execute("SELECT state FROM units WHERE id=?", (h,)).fetchone()[0]
            print(f"[{host}] unit {h} -> {st}", flush=True)
    conn.close()
    print(f"[{host}] loop stopped (STOP)", flush=True)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="serve_fleet.py")
    ap.add_argument("db", nargs="?", default=os.path.join(TFH, "queue.db"))
    ap.add_argument("hosts_file", nargs="?", default=os.path.join(TFH, "hosts_all.txt"))
    ap.add_argument("--base", default="LOCAL")
    args = ap.parse_args()
    db, hosts_file, base = args.db, args.hosts_file, args.base
    # master's local units run on the current checkout; skip the pinned-base goto
    os.environ.setdefault("TFH_SKIP_PREFLIGHT", "1")
    print(f"serve_fleet: db={db} hosts={hosts_file}", flush=True)
    me = socket.gethostname()
    threads = []
    for host, role, caps, slots, lane in _hosts(hosts_file):
        is_master = ("master" in role) or host.startswith(me.split(".")[0])
        gpu_reqs = _LANE_GPU_REQS.get(lane)
        for s in range(slots):
            t = threading.Thread(target=_worker_loop,
                                 args=(host, role, caps, db, base, is_master, gpu_reqs), daemon=True)
            t.start()
            threads.append(t)
        print(f"started {slots} loop(s) for {host} (master={is_master}, caps={caps}, lane={lane})",
              flush=True)
    for t in threads:
        t.join()
    print("fleet drained", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
