#!/usr/bin/env python3
"""Phase-B enqueue: turn dedup'd root causes into fix tasks.

Reads the root_causes persisted by dedup.py, keeps only the ACTIONABLE ones
(fix_class in {code, flaky}), picks a fixer test for each (a de-flake cause
prefers a FLAKY member so the fixer runs the high-N rerun gate), and enqueues one
Phase-B unit per root cause. Infra / wontfix / unknown causes are NOT auto-fixed
here -- they are surfaced in the report for a human to file as tasks.

Idempotent: a root cause that already has a Phase-B unit is skipped, so this is
safe to re-run (and to auto-run right after the dedup report).

Usage: enqueue_b.py [--dry] [--include-infra]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from metrics import classify_cause  # noqa: E402

TFH = os.path.expanduser("~/.tfh")
DB = os.path.join(TFH, "queue.db")

# fix_classes we auto-fix in Phase B. Others (infra/wontfix/unknown) are left for
# a human -- infra fixes are registration/config changes that should not be blind
# code diffs, wontfix are intentional skips, unknown need more diagnosis.
DEFAULT_FIX_CLASSES = {"code", "flaky"}

_GMAP = {"cpu_mockable": "cpu", "unknown": "cpu",
         "single_gpu": "single", "multi_gpu_nccl": "multi"}


def _fixer_test(conn, rcid: int):
    """Pick the test that the fixer will edit + rerun for this root cause.
    Prefer a FLAKY member (its de-flake rerun gate is the verification), else the
    lowest test id for determinism. Returns a tests row dict or None."""
    conn.row_factory = sqlite3.Row
    members = [dict(r) for r in conn.execute(
        "SELECT t.id, t.test_target, t.test_case, t.issue_ids, t.category "
        "FROM root_cause_tests rct JOIN tests t ON rct.test_id=t.id "
        "WHERE rct.root_cause_id=? ORDER BY (t.category='FLAKY') DESC, t.id ASC",
        (rcid,))]
    return members[0] if members else None


def _gpu_req(conn, issue_id: str) -> str:
    row = conn.execute("SELECT gpu_topology FROM test_issue WHERE issue_id=?",
                       (issue_id,)).fetchone()
    topo = (row[0] if row else None) or "cpu_mockable"
    return _GMAP.get(topo, "cpu")


def main() -> int:
    ap = argparse.ArgumentParser(prog="enqueue_b.py")
    ap.add_argument("--dry", action="store_true", help="print what would be enqueued, change nothing")
    ap.add_argument("--include-infra", action="store_true",
                    help="also enqueue infra causes (default: leave for a human)")
    args = ap.parse_args()

    fix_classes = set(DEFAULT_FIX_CLASSES)
    if args.include_infra:
        fix_classes.add("infra")

    conn = sqlite3.connect(DB)
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row

    existing = {r[0] for r in conn.execute(
        "SELECT root_cause_cluster_id FROM units WHERE phase='B' "
        "AND root_cause_cluster_id IS NOT NULL")}

    causes = [dict(r) for r in conn.execute(
        "SELECT id, title, cause_category FROM root_causes")]

    plan, skipped = [], {"already": 0, "no_fixer": 0}
    by_class = {}
    for rc in causes:
        fc = classify_cause(rc["cause_category"])
        by_class[fc] = by_class.get(fc, 0) + 1
        if fc not in fix_classes:
            continue
        if rc["id"] in existing:
            skipped["already"] += 1
            continue
        fixer = _fixer_test(conn, rc["id"])
        if not fixer:
            skipped["no_fixer"] += 1
            continue
        try:
            issue_id = json.loads(fixer["issue_ids"] or "[]")[0]
        except (ValueError, IndexError):
            skipped["no_fixer"] += 1
            continue
        deflake = (fixer["category"] == "FLAKY") or fc == "flaky"
        plan.append({
            "rcid": rc["id"], "fix_class": fc, "deflake": deflake,
            "fixer_test_id": fixer["id"], "issue_id": str(issue_id),
            "gpu_req": _gpu_req(conn, str(issue_id)),
            "target": fixer["test_target"], "case": fixer["test_case"],
            "title": (rc["title"] or "")[:80],
        })

    n_deflake = sum(1 for p in plan if p["deflake"])
    print(f"root causes: {len(causes)} | fix-class {by_class}")
    print(f"eligible ({sorted(fix_classes)}): {len(plan)} to enqueue "
          f"({n_deflake} de-flake) | skipped {skipped}")

    if args.dry:
        for p in plan[:20]:
            print(f"  rc{p['rcid']:>4} {p['fix_class']:>5} "
                  f"{'deflake' if p['deflake'] else '       '} "
                  f"{p['gpu_req']:>6} {p['target'].split(':')[-1]}::{p['case'][:40]}")
        if len(plan) > 20:
            print(f"  ... +{len(plan)-20} more")
        return 0

    enq = 0
    for p in plan:
        conn.execute(
            "INSERT INTO units(test_issue_id, phase, root_cause_cluster_id, state, "
            "gpu_req, attempts, available_at, priority, tried_worker_ids) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (p["issue_id"], "B", p["rcid"], "queued", p["gpu_req"], 0, 0,
             10.0 if p["deflake"] else 5.0, "[]"))
        conn.execute(
            "UPDATE root_causes SET fixer_test_id=?, fix_state='in_progress' WHERE id=?",
            (p["fixer_test_id"], p["rcid"]))
        enq += 1
    print(f"enqueued {enq} Phase-B units")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
