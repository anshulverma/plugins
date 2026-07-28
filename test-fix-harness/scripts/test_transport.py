#!/usr/bin/env python3
"""Slice 5/6 wiring test: routing + local_transport via run_unit.sh with a faked
claude (TFH_DRYRUN_FAKE_CLAUDE) so no real model/SSH is needed. `python3 test_transport.py`."""
from __future__ import annotations

import os
import tempfile

import dispatcher
import migrate
import transport


def main() -> int:
    # --- routing rules ---
    assert transport.allowed_gpu_reqs('"cpu,gpu"') == {"cpu", "single", "multi"}
    assert transport.allowed_gpu_reqs("cpu") == {"cpu"}
    assert transport.route_for("single") == "local"
    assert transport.route_for("multi") == "local_multi"
    assert transport.route_for("cpu") == "cpu"

    tmp = tempfile.mkdtemp(prefix="tfh_tr_")
    db = os.path.join(tmp, "queue.db")
    migrate.apply_migrations(db)
    conn = dispatcher.connect(db)
    tfh = os.path.join(tmp, "tfh")
    os.environ["TFH_SKIP_PREFLIGHT"] = "1"
    os.environ["TFH_DRYRUN_FAKE_CLAUDE"] = "1"
    try:
        # a cpu-capable OD may only claim cpu units; the gpu master claims any.
        conn.execute("INSERT INTO units(id, phase, state, gpu_req, attempts, available_at, "
                     "priority, tried_worker_ids, test_issue_id) VALUES "
                     "(1,'A','queued','cpu',0,0,1.0,'[]','I1'),"
                     "(2,'A','queued','single',0,0,2.0,'[]','I2')")
        conn.execute("INSERT INTO test_issue(issue_id, target, test_name, category, gpu_topology, triage_class) "
                     "VALUES ('I1','//a:b','case1','FAILURE','cpu_mockable','actionable'),"
                     "('I2','//c:d','case2','SKIPPING','single_gpu','actionable')")

        def local(env, unit):
            return transport.local_transport(env, unit, tfh=tfh)

        # OD (cpu-only) skips the single-GPU unit, claims the cpu unit.
        h = transport.serve_once_for_host(conn, "od1", "cpu", local, "BASE", 60, tfh=tfh)
        assert h == 1, f"cpu host should claim cpu unit 1, got {h}"
        assert conn.execute("SELECT state FROM units WHERE id=1").fetchone()[0] == "done"

        # GPU master claims the remaining single-GPU unit and runs it locally.
        h2 = transport.serve_once_for_host(conn, "master", '"cpu,gpu"', local, "BASE", 60, tfh=tfh)
        assert h2 == 2, f"gpu host should claim single-gpu unit 2, got {h2}"
        assert conn.execute("SELECT state FROM units WHERE id=2").fetchone()[0] == "done"

        # nothing left
        assert transport.serve_once_for_host(conn, "od2", "cpu", local, "BASE", 60, tfh=tfh) is None
    finally:
        conn.close()
        os.environ.pop("TFH_DRYRUN_FAKE_CLAUDE", None)

    print("PASS test_transport: routing + capability-aware claim + local_transport (fake claude)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
