#!/usr/bin/env python3
"""Slice 7 test: deterministic fingerprint merge, many-to-many test<->cause,
insufficient-evidence singletons, non-repro clustering, deterministic output,
and FK-junction persistence. `python3 test_reduce.py`."""
from __future__ import annotations

import json
import os
import tempfile

import dispatcher
import migrate
import reduce as reduce_mod


def _rc(culprit, cat, sig, err=None):
    return {"culprit_symbol": culprit, "cause_category": cat, "signature": sig,
            "error_signature": err, "mechanism": None, "observed_frequency": 1.0}


def _mk_test(conn, target, case):
    conn.execute(
        "INSERT INTO tests(test_target, test_case, issue_ids, category, status) VALUES (?,?,?,?,?)",
        (target, case, "[]", "FAILURE", "pending"),
    )
    return conn.execute("SELECT id FROM tests WHERE test_target=? AND test_case=?",
                        (target, case)).fetchone()[0]


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="tfh_reduce_")
    db = os.path.join(tmp, "queue.db")
    migrate.apply_migrations(db)
    conn = dispatcher.connect(db)
    try:
        t1 = _mk_test(conn, "//m:x", "a")
        t2 = _mk_test(conn, "//m:x", "b")
        t3 = _mk_test(conn, "//m:y", "c")
        t4 = _mk_test(conn, "//m:z", "d")

        entries = [
            # t1 and t2 share cause sigA; t1 ALSO has a second cause sigC.
            {"test_id": t1, "report": {"unit_id": "1", "status": "completed",
                "root_causes": [_rc("modX.py:foo", "logic", "sigA"),
                                _rc("modX.py:bar", "logic", "sigC")]}},
            {"test_id": t2, "report": {"unit_id": "2", "status": "completed",
                "root_causes": [_rc("modX.py:foo", "logic", "sigA")]}},
            # t3 different cause.
            {"test_id": t3, "report": {"unit_id": "3", "status": "completed",
                "root_causes": [_rc("modY.py:baz", "timeout", "sigB")]}},
            # t4 non-reproduced with no root cause -> insufficient-evidence singleton.
            {"test_id": t4, "report": {"unit_id": "4", "status": "completed",
                "reproduced": False, "root_causes": []}},
        ]

        clusters = reduce_mod.reduce(entries)
        by_sig = {c["signature"]: c for c in clusters}

        # sigA merged t1+t2 (biggest cluster, first).
        assert clusters[0]["signature"] == "sigA" and clusters[0]["size"] == 2
        assert sorted(by_sig["sigA"]["test_ids"]) == sorted([t1, t2])
        # many-to-many: t1 appears in both sigA and sigC.
        assert t1 in by_sig["sigA"]["test_ids"] and t1 in by_sig["sigC"]["test_ids"]
        # conflicting/different cause stays separate.
        assert by_sig["sigB"]["test_ids"] == [t3]
        # insufficient-evidence singleton for the non-repro test.
        ins = by_sig[f"insufficient:test{t4}"]
        assert ins["review_flag"] == "insufficient-evidence" and ins["test_ids"] == [t4]

        # Deterministic: reduce again -> identical JSON.
        assert json.dumps(reduce_mod.reduce(entries)) == json.dumps(clusters)

        # Persist -> root_causes + junction reflect the many-to-many mapping.
        reduce_mod.persist(conn, clusters)
        n_causes = conn.execute("SELECT COUNT(*) FROM root_causes").fetchone()[0]
        assert n_causes == len(clusters), (n_causes, len(clusters))
        # t1 linked to exactly 2 causes (sigA, sigC).
        n_t1 = conn.execute(
            "SELECT COUNT(*) FROM root_cause_tests WHERE test_id=?", (t1,)
        ).fetchone()[0]
        assert n_t1 == 2, n_t1

        # Report files written deterministically.
        jpath, mpath = reduce_mod.write_report(clusters, "run1", os.path.join(tmp, "reduce"))
        assert os.path.getsize(jpath) > 0 and os.path.getsize(mpath) > 0
    finally:
        conn.close()

    print("PASS test_reduce: fingerprint merge + many-to-many + insufficient-evidence + deterministic + junction")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
