#!/usr/bin/env python3
"""Top-level entrypoint for the distributed test-fix harness.

Give it an oncall (test owner). It:
  1. INGESTS the oncall's open test issues (triage -> queue.db, dedup, classify),
  2. prints a REPORT (counts by category / gpu topology / pre-cluster),
  3. ESTIMATES wall-clock to diagnose + fix with 1, 2, 4, ... workers,
  4. optionally STARTS the run (master loop + any --workers you name).
Workers can be added/removed anytime with add_worker.sh / by stopping a loop;
the queue is the shared source of truth so nothing needs a restart.

Usage:
  harness.py <oncall>                 # ingest, report, estimate, start (local master only)
  harness.py <oncall> --workers a,b   # also provision + launch those remote hosts
  harness.py <oncall> --dry-run       # report + estimate only, do not start
Add/remove capacity anytime: add_worker.sh <host>  /  kill the host's serve_<tag> loop.
"""
from __future__ import annotations

import argparse
import math
import os
import subprocess
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import triage  # noqa: E402

TFH = os.path.expanduser("~/.tfh")

# Rough per-unit wall-clock (minutes) - tunable; refined from real turnaround later.
T_DIAG = 5.0          # Phase-A diagnose (buck build + testx-debug), median-ish
T_FIX = 15.0          # Phase-B fix + self-fix loop + verify + jf submit
DEDUP_MIN = 10.0      # master-side reduce/dedup, roughly flat
CAUSE_FRACTION = 0.4  # fraction of units that become unique code-fixable causes after dedup


def _fmt(mins: float) -> str:
    mins = int(round(mins))
    if mins < 60:
        return f"{mins}m"
    return f"{mins // 60}h{mins % 60:02d}m"


def report(db: str) -> dict:
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    n_issue = c.execute("SELECT COUNT(*) FROM test_issue").fetchone()[0]
    n_units = c.execute("SELECT COUNT(*) FROM units").fetchone()[0]
    states = dict(c.execute("SELECT state, COUNT(*) FROM units GROUP BY state"))
    by_cat = dict(c.execute("SELECT category, COUNT(*) FROM test_issue GROUP BY category"))
    by_topo = dict(c.execute("SELECT gpu_topology, COUNT(*) FROM test_issue GROUP BY gpu_topology"))
    dupes = c.execute("SELECT COUNT(*) FROM tests WHERE json_array_length(issue_ids) > 1").fetchone()[0]
    clusters = c.execute(
        "SELECT cluster_hint, COUNT(*) n FROM test_issue GROUP BY cluster_hint "
        "ORDER BY n DESC LIMIT 5").fetchall()
    remaining = states.get("queued", 0) + states.get("dispatched", 0)

    print(f"\n=== Report: {n_issue} open issues -> {n_units} canonical tests "
          f"({dupes} duplicate issues collapsed) ===")
    print(f"  state:     {states}")
    print(f"  category:  {by_cat}")
    print(f"  gpu:       {by_topo}")
    print(f"  remaining to process: {remaining}  (done={states.get('done',0)}, parked={states.get('parked',0)})")
    print(f"  top pre-clusters (share a likely cause): {[(r['cluster_hint'][:8], r['n']) for r in clusters]}")
    return {"units": n_units, "remaining": remaining}


def estimate(units: int) -> None:
    causes = max(1, round(units * CAUSE_FRACTION))
    print(f"\n=== Time estimate ({units} tests; assumes ~{T_DIAG:.0f}m/diagnose, "
          f"~{T_FIX:.0f}m/fix, ~{causes} code-fixable causes after dedup; buck2 => 1 test/host) ===")
    print(f"  {'workers':>7} | {'diagnose':>9} | {'dedup':>6} | {'fix':>7} | {'total':>7}")
    for w in (1, 2, 3, 4, 6, 8, 10, 16):
        diag = math.ceil(units / w) * T_DIAG
        fix = math.ceil(causes / w) * T_FIX
        total = diag + DEDUP_MIN + fix
        print(f"  {w:>7} | {_fmt(diag):>9} | {_fmt(DEDUP_MIN):>6} | {_fmt(fix):>7} | {_fmt(total):>7}")
    print("  (rough; refine with real turnaround from `metrics.py` after the first ~20 tests.)")


def start(db: str, workers: list[str]) -> None:
    # provision + launch any named remote workers (needs SSH from your login shell)
    for host in workers:
        if not host:
            continue
        print(f"\n[start] add_worker {host} ...")
        subprocess.run(["python3", os.path.join(HERE, "add_worker.py"), host], check=False)
    # launch the master loop (local, no SSH)
    print("\n[start] launching master serve_fleet (1 slot, local) ...")
    env = dict(os.environ, TFH_SKIP_PREFLIGHT="1")
    log = open(os.path.join(TFH, "logs", "serve_master_fleet.log"), "a")
    subprocess.Popen(["python3", os.path.join(HERE, "serve_fleet.py"), db,
                      os.path.join(TFH, "hosts_master.txt")],
                     cwd="/data/users/anshulverma/fbsource/fbcode",
                     stdout=log, stderr=log, env=env, start_new_session=True)
    print("[start] master loop launched. Add more hosts anytime: add_worker.sh <host>")
    print(f"[start] watch: python3 {HERE}/dispatcher.py --db {db} progress --watch")


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="harness.py",
        description="harness.py <oncall> [--workers h1,h2]  ->  ingest, report, "
                    "estimate, and start. No workers = local master only.")
    ap.add_argument("oncall", help="test owner / oncall shortname (e.g. mitra_training)")
    ap.add_argument("--workers", default="",
                    help="optional comma-separated remote hosts to add; omit for local-only")
    ap.add_argument("--dry-run", action="store_true", help="report + estimate only; do not start")
    ap.add_argument("--no-ingest", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--db", default=None, help=argparse.SUPPRESS)  # optional override
    args = ap.parse_args()
    # single active run in the default location (one oncall at a time); no need to pass it
    db = args.db or os.path.join(TFH, "queue.db")
    os.makedirs(os.path.join(TFH, "logs"), exist_ok=True)

    if not args.no_ingest:
        print(f"[harness] ingesting open test issues for oncall '{args.oncall}' -> {db} ...")
        counts = triage.seed(db, args.oncall, None, os.path.join(TFH, "dry_run_manifest.json"))
        print(f"[harness] ingested: {counts}")

    stats = report(db)
    estimate(stats["remaining"] or stats["units"])

    if args.dry_run:
        print("\n(dry-run: not starting. Drop --dry-run to begin.)")
    else:
        start(db, [h.strip() for h in args.workers.split(",") if h.strip()])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
