#!/usr/bin/env python3
"""Slice 9 test: observability surface (status_report / show / health / attention
/ run_sql) over a seeded queue.db. `python3 test_observability.py`."""
from __future__ import annotations

import os
import tempfile

import dispatcher
import migrate


def _unit(conn, uid, state, route=None, park_cat=None, issue=None):
    conn.execute(
        "INSERT INTO units(id, phase, state, gpu_req, attempts, available_at, "
        "priority, tried_worker_ids, route, park_category, test_issue_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (uid, "A", state, "cpu", 0, 0, 1.0, "[]", route, park_cat, issue),
    )


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="tfh_obs_")
    db = os.path.join(tmp, "queue.db")
    migrate.apply_migrations(db)
    conn = dispatcher.connect(db)
    try:
        _unit(conn, 1, "done")
        _unit(conn, 2, "done")
        _unit(conn, 3, "parked", park_cat="gpu_capacity")
        _unit(conn, 4, "parked", park_cat="infra_exhausted")
        _unit(conn, 5, "dispatched", route="re_multi")
        _unit(conn, 6, "queued", issue="ISSUE_X")
        conn.execute("INSERT INTO test_issue(issue_id, target, test_name, category, "
                     "gpu_topology, triage_class) VALUES (?,?,?,?,?,?)",
                     ("ISSUE_X", "//a:b", "case", "FAILURE", "cpu_mockable", "actionable"))
        conn.execute("INSERT INTO workers(worker_id, host, state) VALUES ('w1','h1','down')")
        conn.execute("INSERT INTO workers(worker_id, host, state) VALUES ('w2','h2','down')")

        rep = dispatcher.status_report(conn)
        assert rep["by_state"]["done"] == 2 and rep["by_state"]["parked"] == 2
        assert rep["parked_by_category"]["gpu_capacity"] == 1
        assert rep["dispatched_by_route"]["re_multi"] == 1

        banners = dispatcher.attention(conn)
        assert any("all workers down" in b for b in banners), banners
        assert any("parked on GPU capacity" in b for b in banners), banners

        s = dispatcher.show(conn, "ISSUE_X")
        assert s["test_issue"] is not None and s["test_issue"]["gpu_topology"] == "cpu_mockable"
        assert [u["id"] for u in s["units"]] == [6]

        h = dispatcher.health(conn)
        assert h["units_total"] == 6 and h["units_done"] == 2 and h["in_flight"] == 1

        rows = dispatcher.run_sql(conn, "SELECT COUNT(*) FROM units")
        assert rows[0][0] == 6
        try:
            dispatcher.run_sql(conn, "DELETE FROM units")
            raise AssertionError("run_sql must reject non-SELECT")
        except ValueError:
            pass
    finally:
        conn.close()

    print("PASS test_observability: status_report/show/health/attention/run_sql")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
