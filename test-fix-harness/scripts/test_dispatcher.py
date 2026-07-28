#!/usr/bin/env python3
"""Slice 5 test: dispatch state machine via an L0 mock-worker (no SSH).

Covers claim CAS + worker anti-affinity, terminal done, infra requeue+backoff
to park, lease reaper, crash recovery, and NEEDS_GPU_EXEC reroute.
`python3 test_dispatcher.py`.
"""
from __future__ import annotations

import os
import tempfile

import dispatcher
import migrate


def _seed_unit(conn, uid, priority=1.0, phase="A", gpu="cpu"):
    conn.execute(
        "INSERT INTO units(id, phase, state, gpu_req, attempts, available_at, "
        "priority, tried_worker_ids) VALUES (?,?,?,?,?,?,?,?)",
        (uid, phase, "queued", gpu, 0, 0, priority, "[]"),
    )


def ok_transport(env, unit):
    return {"unit_id": env["unit_id"], "status": "completed",
            "reproduced": True, "root_causes": []}, 0


def fail_transport(env, unit):
    return None, 1  # infra failure


def reroute_transport(env, unit):
    return {"unit_id": env["unit_id"], "status": "NEEDS_GPU_EXEC"}, 0


def state_of(conn, uid):
    return conn.execute("SELECT state FROM units WHERE id=?", (uid,)).fetchone()[0]


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="tfh_disp_")
    db = os.path.join(tmp, "queue.db")
    migrate.apply_migrations(db)
    conn = dispatcher.connect(db)

    def reset():
        conn.execute("DELETE FROM units")

    try:
        # --- happy path: claim -> dispatched -> done ---
        reset()
        _seed_unit(conn, 1)
        handled = dispatcher.dispatch_once(conn, "w1", ok_transport, now=100)
        assert handled == 1 and state_of(conn, 1) == "done", state_of(conn, 1)

        # --- infra failure requeues with backoff, then parks after max_attempts ---
        reset()
        _seed_unit(conn, 2)
        # attempt 1 (fail) -> requeued, available_at pushed out
        dispatcher.dispatch_once(conn, "w1", fail_transport, now=200)
        assert state_of(conn, 2) == "queued"
        row = conn.execute("SELECT attempts, available_at FROM units WHERE id=2").fetchone()
        assert row[0] == 1 and row[1] > 200, row
        # attempt 2 (fail, different worker due to anti-affinity)
        dispatcher.dispatch_once(conn, "w2", fail_transport, now=row[1])
        assert conn.execute("SELECT attempts FROM units WHERE id=2").fetchone()[0] == 2
        # attempt 3 (fail) -> parked
        av = conn.execute("SELECT available_at FROM units WHERE id=2").fetchone()[0]
        dispatcher.dispatch_once(conn, "w3", fail_transport, now=av)
        assert state_of(conn, 2) == "parked", state_of(conn, 2)
        assert conn.execute("SELECT park_category FROM units WHERE id=2").fetchone()[0] == "infra_exhausted"

        # --- anti-affinity: a worker that already tried a unit cannot reclaim it ---
        reset()
        _seed_unit(conn, 3)
        dispatcher.dispatch_once(conn, "w1", fail_transport, now=300)  # w1 tried unit 3
        avail = conn.execute("SELECT available_at FROM units WHERE id=3").fetchone()[0]
        assert dispatcher.claim_unit(conn, "w1", now=avail) is None, "w1 must be excluded"
        claimed = dispatcher.claim_unit(conn, "w2", now=avail)
        assert claimed is not None and claimed["id"] == 3, "w2 should claim it"

        # --- reaper: an expired in-flight lease is requeued ---
        # unit 3 is now 'dispatched' (claimed by w2 above); expire its lease.
        conn.execute("UPDATE units SET lease_expires_at=? WHERE id=3", (avail + 1,))
        n = dispatcher.reap_leases(conn, now=avail + 10)
        assert n == 1 and state_of(conn, 3) == "queued", (n, state_of(conn, 3))

        # --- crash recovery: dispatched units go back to queued ---
        reset()
        _seed_unit(conn, 4)
        dispatcher.claim_unit(conn, "w9", now=400)
        assert state_of(conn, 4) == "dispatched"
        recovered = dispatcher.recover(conn)
        assert recovered >= 1 and state_of(conn, 4) == "queued"

        # --- reroute: NEEDS_GPU_EXEC re-queues without attempt penalty ---
        reset()
        _seed_unit(conn, 5, gpu="multi")
        dispatcher.dispatch_once(conn, "w1", reroute_transport, now=500)
        assert state_of(conn, 5) == "queued"
        assert conn.execute("SELECT attempts FROM units WHERE id=5").fetchone()[0] == 0

        # --- status counts reflect terminal states ---
        reset()
        _seed_unit(conn, 6)
        _seed_unit(conn, 7)
        dispatcher.dispatch_once(conn, "w1", ok_transport, now=600)
        counts = dispatcher.status_counts(conn)
        assert counts.get("done") == 1 and counts.get("queued") == 1, counts
    finally:
        conn.close()

    print("PASS test_dispatcher: claim/anti-affinity/requeue-backoff/park/reap/recover/reroute")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
