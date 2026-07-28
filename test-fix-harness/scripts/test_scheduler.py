#!/usr/bin/env python3
"""Slice 6 test: partitioned GPU/RE leases - no multi-GPU starvation, local-first
with RE fallback, overflow parks, reaper frees permits, budgets. `python3 test_scheduler.py`."""
from __future__ import annotations

import os
import tempfile

import dispatcher
import migrate
import scheduler

CFG = dict(scheduler.DEFAULT_CFG)
CFG.update(local_gpu_slots=1, re_single_gpu_fallback_slots=1, re_multi_gpu_slots=2,
           local_wait_timeout_s=90, gpu_ttl_s=1800)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="tfh_sched_")
    db = os.path.join(tmp, "queue.db")
    migrate.apply_migrations(db)
    conn = dispatcher.connect(db)

    def reset():
        conn.execute("DELETE FROM leases")

    try:
        # --- single-GPU tries local first ---
        reset()
        assert scheduler.acquire(conn, "single", "u1", "w1", now=100, cfg=CFG) == "local"
        # local now full (1 slot): a second single with no wait cannot fall back yet
        assert scheduler.acquire(conn, "single", "u2", "w2", now=100, cfg=CFG, waited_s=0) is None
        # after local_wait_timeout, it falls back to the RE single-gpu pool
        assert scheduler.acquire(conn, "single", "u2", "w2", now=100, cfg=CFG, waited_s=90) == "re_fallback"
        # RE single fallback now full too -> next single parks
        assert scheduler.acquire(conn, "single", "u3", "w3", now=100, cfg=CFG, waited_s=90) is None

        # --- no starvation: a single-GPU flood cannot touch the multi pool ---
        # (local + re_single are saturated above); multi still has its own permits
        assert scheduler.acquire(conn, "multi", "m1", "w4", now=100, cfg=CFG) == "re_multi"
        assert scheduler.acquire(conn, "multi", "m2", "w5", now=100, cfg=CFG) == "re_multi"
        # multi pool (2) now full -> 3rd multi parks
        assert scheduler.acquire(conn, "multi", "m3", "w6", now=100, cfg=CFG) is None

        # --- release frees a permit; next acquire succeeds ---
        assert scheduler.release(conn, "m1") == 1
        assert scheduler.acquire(conn, "multi", "m3", "w6", now=100, cfg=CFG) == "re_multi"

        # --- reaper reclaims expired permits (crashed worker), no leak ---
        reset()
        scheduler.acquire(conn, "multi", "x1", "w1", now=1000, cfg=CFG)  # expires at 1000+ttl
        assert scheduler.used(conn, "re_multi_gpu") == 1
        freed = scheduler.reap(conn, now=1000 + CFG["gpu_ttl_s"] + 1)
        assert freed == 1 and scheduler.used(conn, "re_multi_gpu") == 0

        # --- cpu needs no permit ---
        reset()
        assert scheduler.acquire(conn, "cpu", "c1", "w1", now=1, cfg=CFG) == "cpu"
        assert scheduler.used(conn, "local_gpu") == 0

        # --- budgets ---
        assert scheduler.unit_budget_ok(29, CFG) and not scheduler.unit_budget_ok(30, CFG)
        assert scheduler.daily_budget_ok(599, CFG) and not scheduler.daily_budget_ok(600, CFG)
    finally:
        conn.close()

    print("PASS test_scheduler: partitioned leases, local-first+fallback, no starvation, reaper, budgets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
