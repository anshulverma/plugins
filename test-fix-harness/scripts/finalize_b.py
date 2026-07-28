#!/usr/bin/env python3
"""Phase-B finalize: backfill published diffs onto root causes, then emit the
end-of-run metrics report (+ paste).

Backfill is idempotent: it scans every done Phase-B unit's result.json and writes
its diff_url / fix_state onto the linked root cause, so the funnel is correct even
for units recorded by a daemon that predates the in-line propagation. Then it runs
metrics.py --paste for the full run report (velocity, churn, dedup, root-cause
ranking, diff count) with embedded charts.

Usage: finalize_b.py [--no-paste]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys

TFH = os.path.expanduser("~/.tfh")
DB = os.path.join(TFH, "queue.db")
SCRIPTS = os.path.dirname(os.path.abspath(__file__))


def backfill(conn) -> dict:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, root_cause_cluster_id FROM units "
        "WHERE phase='B' AND state='done' AND root_cause_cluster_id IS NOT NULL").fetchall()
    counts = {"diff_published": 0, "diff_local_only": 0, "no_result": 0, "no_diff": 0}
    for r in rows:
        p = os.path.join(TFH, "units", str(r["id"]), "result.json")
        try:
            res = json.load(open(p))
        except (OSError, ValueError):
            counts["no_result"] += 1
            continue
        status = res.get("status")
        fix_state = "diff_published" if status == "completed" else "diff_local_only"
        diff_url = res.get("diff_url")
        if not diff_url and status != "completed":
            counts["no_diff"] += 1
        conn.execute("UPDATE root_causes SET diff_url=?, fix_state=? WHERE id=?",
                     (diff_url, fix_state, r["root_cause_cluster_id"]))
        counts[fix_state] += 1
    conn.commit()
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(prog="finalize_b.py")
    ap.add_argument("--no-paste", action="store_true", help="write report locally, do not paste")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    conn.isolation_level = None
    counts = backfill(conn)
    print(f"backfill: {counts}")
    conn.close()

    cmd = ["python3", os.path.join(SCRIPTS, "metrics.py"), DB,
           "--title", "Mitra test-fix harness - end-of-run report (Phase A diagnose + Phase B fix)"]
    if not args.no_paste:
        cmd.append("--paste")
    r = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
