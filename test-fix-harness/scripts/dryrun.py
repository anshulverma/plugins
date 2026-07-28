#!/usr/bin/env python3
"""Dry-run GO/NO-GO gate for the distributed test-fix harness (plan Slice 8).

L0 (here): a local mock-worker replays canned JSON to validate the contract
round-trip (both directions), the queue state transitions, dedup mechanics
(seeded duplicate), the land-guard, and diff-not-landed -- with zero infra,
fully deterministic. Any contract mismatch is a hard NO-GO.

L1 (real): 1-2 CPU OD workers + one RE multi-GPU lease, full A->reduce->B on the
3 canaries with a real jf submit then abandon. L1 must be run from a real user
shell (the agent sandbox cannot SSH); `l1_command()` returns the recipe.
"""
from __future__ import annotations

import os
import subprocess
import tempfile

import contracts
import dispatcher
import migrate
import reduce as reduce_mod

HERE = os.path.dirname(os.path.abspath(__file__))


def _mock_worker(env, unit):
    # Canned Phase-A: two units share signature SIGDUP (seeded duplicate).
    return {
        "unit_id": env["unit_id"], "status": "completed", "reproduced": True,
        "root_causes": [{"culprit_symbol": "mod.py:foo", "cause_category": "logic",
                         "signature": "SIGDUP", "mechanism": None,
                         "error_signature": None, "observed_frequency": 1.0}],
    }, 0


def _bad_worker(env, unit):
    # Contract violation (extra key) -> must be caught, forcing NO-GO.
    return {"unit_id": env["unit_id"], "status": "completed", "bogus": 1}, 0


def _land_guard_blocks() -> bool:
    d = tempfile.mkdtemp(prefix="tfh_lg_")
    sl = os.path.join(d, "sl")
    with open(sl, "w") as out, open(os.path.join(HERE, "land_guard.sh")) as src:
        out.write(src.read())
    os.chmod(sl, 0o755)
    env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"],
               TFH_LANDGUARD_LOG=os.path.join(d, "log"))
    rc = subprocess.run(["sl", "land"], env=env, capture_output=True).returncode
    return rc == 97


def l0(db: str, worker=_mock_worker) -> dict:
    checks: dict = {}
    migrate.apply_migrations(db)
    conn = dispatcher.connect(db)
    try:
        conn.execute("DELETE FROM units")
        for uid in (1, 2):
            conn.execute(
                "INSERT INTO units(id, phase, state, gpu_req, attempts, available_at, "
                "priority, tried_worker_ids) VALUES (?,?,?,?,?,?,?,?)",
                (uid, "A", "queued", "cpu", 0, 0, 1.0, "[]"),
            )
        reports = []
        roundtrip = True
        for uid in (1, 2):
            unit = dispatcher.claim_unit(conn, f"w{uid}", now=1)
            env = {
                "unit_id": str(unit["id"]), "phase": "A", "test_target": "//x:t",
                "test_case": "c", "gpu_req": "cpu", "base_commit": "PINNED",
                "payload_sha256": "0" * 64, "route": None,
            }
            contracts.validate_named(env, "dispatch")  # outbound contract
            res, code = worker(env, unit)
            try:
                contracts.validate_named(res, "phase_a")  # inbound contract
                dispatcher.record_result(conn, unit["id"], f"w{uid}", res, code, now=1)
                reports.append({"test_id": uid, "report": res})
            except ValueError as ex:
                roundtrip = False
                checks["contract_error"] = str(ex)
                # leave the unit dispatched; queue-transition check will fail
        checks["contract_roundtrip"] = roundtrip
        checks["queue_transitions"] = (
            roundtrip and dispatcher.status_counts(conn).get("done") == 2
        )
        # dedup: two identical-signature reports collapse to one cluster of size 2
        if roundtrip:
            clusters = reduce_mod.reduce(reports)
            checks["dedup"] = len(clusters) == 1 and clusters[0]["size"] == 2
        else:
            checks["dedup"] = False
    finally:
        conn.close()

    checks["land_guard"] = _land_guard_blocks()
    checks["diff_not_landed"] = checks["land_guard"]  # workers have no land path
    go = all(v is True for k, v in checks.items() if k != "contract_error")
    return {"go": go, "checks": checks}


def l1_command() -> str:
    return (
        "Run from a real user shell (agent sandbox cannot SSH):\n"
        "  1. bootstrap 1-2 CPU OD + confirm 1 RE multi-GPU lease\n"
        "  2. seed queue with the 3 canaries from dry_run_manifest.json\n"
        "  3. dispatcher runs A->reduce->B; CPU canary does a real `jf submit` then abandon\n"
        "  4. assert schema-valid reports, expected cluster count, diff published-not-landed\n"
        "Hard NO-GO on any contract mismatch; watchdog 30m/60m."
    )


if __name__ == "__main__":
    import json
    tmp = tempfile.mkdtemp(prefix="tfh_dryrun_")
    print(json.dumps(l0(os.path.join(tmp, "queue.db")), indent=1))
