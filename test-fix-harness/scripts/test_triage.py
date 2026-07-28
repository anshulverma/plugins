#!/usr/bin/env python3
"""Slice 2 acceptance test for the Phase-0 triage seeder (offline, deterministic).

Run: `python3 test_triage.py` (exit 0 = pass). Uses test_triage_fixture.json so
it needs no live `meta` CLI.
"""
from __future__ import annotations

import json
import os
import tempfile

import migrate
import triage

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "test_triage_fixture.json")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="tfh_triage_")
    db = os.path.join(tmp, "queue.db")
    manifest = os.path.join(tmp, "dry_run_manifest.json")

    counts = triage.seed(db, "mitra_training", FIXTURE, manifest)

    # 8 source issues, one (target,case) duplicated (issue 1 & 3) -> 7 canonical.
    assert counts["issues"] == 8, counts
    assert counts["canonical_tests"] == 7, counts
    assert counts["duplicates_collapsed"] == 1, counts

    conn = migrate.connect(db)
    try:
        # Every test_issue row has triage_class and gpu_topology populated.
        bad = conn.execute(
            "SELECT COUNT(*) FROM test_issue WHERE triage_class IS NULL OR gpu_topology IS NULL"
        ).fetchone()[0]
        assert bad == 0, f"{bad} rows missing triage_class/gpu_topology"

        # gpu_topology classified as expected on representative rows.
        topo = dict(conn.execute("SELECT issue_id, gpu_topology FROM test_issue"))
        assert topo["1"] == "cpu_mockable", topo
        assert topo["4"] == "single_gpu", topo   # _GPU
        assert topo["5"] == "multi_gpu_nccl", topo  # nccl

        # Duplicate collapsed: the loop_utils test_is_done row carries 2 issue_ids.
        ids = conn.execute(
            "SELECT issue_ids FROM tests WHERE test_target=? AND test_case LIKE ?",
            ("fbcode//torchtnt/tests/framework:test_loop_utils", "test_is_done%"),
        ).fetchone()[0]
        assert sorted(json.loads(ids)) == ["1", "3"], ids

        # fluent2 predictor variants (7,8) share one cluster_hint (package-level).
        ch = dict(conn.execute("SELECT issue_id, cluster_hint FROM test_issue"))
        assert ch["7"] == ch["8"], "fluent2 amd+mtia should pre-cluster together"
    finally:
        conn.close()

    # Canary selection is deterministic and covers the 3 lanes.
    m1 = json.load(open(manifest))
    recs = triage.build_records(json.load(open(FIXTURE)))
    m2 = triage.select_canaries(recs)
    assert m1 == m2, "canary selection must be deterministic"
    assert m1["cpu"]["gpu_topology"] == "cpu_mockable"
    assert m1["single_gpu"]["gpu_topology"] == "single_gpu"
    assert m1["multi_gpu"]["gpu_topology"] == "multi_gpu_nccl"

    # Enqueue-time validator rejects an impossible topology/route combo.
    try:
        triage.validate_unit("multi_gpu_nccl", "cpu")
        raise AssertionError("validator should reject multi_gpu_nccl on cpu route")
    except ValueError:
        pass

    print("PASS test_triage: classify + dedup + pre-cluster + deterministic canaries + validator")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
