#!/usr/bin/env python3
"""Watchdog: requeue units stuck in-flight past a per-lane timeout, WITHOUT
restarting the serve loops. Touches only queue.db (no SSH), so it can run on the
master alongside everything else. Dispatch time comes from the unit's
payload.json mtime (fallback: lease_expires_at - default TTL).

A requeued unit goes to a different worker (anti-affinity); after max_attempts it
parks (category 'timeout') for human review. Stop via ~/.tfh/STOP.

Usage: watchdog.py <db> [--interval 60] [--cpu 1200] [--single 1800] [--multi 3600]
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import dispatcher  # noqa: E402

TFH = os.path.expanduser("~/.tfh")
STOP = os.path.join(TFH, "STOP")


def _dispatch_ts(uid, lease_expires_at, ttl=1800):
    p = os.path.join(TFH, "units", str(uid), "payload.json")
    try:
        return os.path.getmtime(p)
    except OSError:
        return (lease_expires_at - ttl) if lease_expires_at else None


def sweep(conn, timeouts, now=None) -> int:
    now = now or time.time()
    conn.row_factory = __import__("sqlite3").Row
    stuck = 0
    inflight = conn.execute(
        "SELECT id, gpu_req, lease_expires_at FROM units WHERE state='dispatched'").fetchall()
    for r in inflight:
        dt = _dispatch_ts(r["id"], r["lease_expires_at"])
        if dt is None:
            continue
        limit = timeouts.get(r["gpu_req"], timeouts["cpu"])
        elapsed = now - dt
        if elapsed > limit:
            unit = dispatcher._get_unit(conn, r["id"])
            res = dispatcher.requeue(conn, unit, now, reason=f"timeout_{int(elapsed)}s_gt_{limit}")
            print(f"[watchdog] unit {r['id']} ({r['gpu_req']}) stuck {int(elapsed)}s > {limit}s -> {res}",
                  flush=True)
            stuck += 1
    return stuck


def main() -> int:
    ap = argparse.ArgumentParser(prog="watchdog.py")
    ap.add_argument("db")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--cpu", type=int, default=1200)      # 20 min (median ~3m, p90 ~6m)
    ap.add_argument("--single", type=int, default=1800)   # 30 min
    ap.add_argument("--multi", type=int, default=3600)    # 60 min
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    timeouts = {"cpu": args.cpu, "single": args.single, "multi": args.multi}
    conn = dispatcher.connect(args.db)
    print(f"[watchdog] timeouts={timeouts} interval={args.interval}s", flush=True)
    while True:
        n = sweep(conn, timeouts)
        if args.once or os.path.exists(STOP):
            print("[watchdog] stopping", flush=True)
            break
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
