#!/usr/bin/env python3
"""Slice 3 test: strict contract validation accepts good payloads and rejects
missing/extra/typed/enum violations in both directions. `python3 test_contracts.py`."""
from __future__ import annotations

import contracts


def _rejects(inst, name, why):
    try:
        contracts.validate_named(inst, name)
    except ValueError:
        return
    raise AssertionError(f"expected rejection ({why}) for {name}: {inst}")


def main() -> int:
    # --- dispatch envelope ---
    good_dispatch = {
        "unit_id": "u1", "phase": "A", "test_target": "//t:x", "test_case": "case",
        "gpu_req": "multi", "base_commit": "abc123", "payload_sha256": "deadbeef",
        "route": None, "timeout_s": 900, "cluster_id": None, "prior_attempts": [],
    }
    contracts.validate_named(good_dispatch, "dispatch")
    _rejects({**good_dispatch, "extra": 1}, "dispatch", "extra key")
    _rejects({k: v for k, v in good_dispatch.items() if k != "unit_id"}, "dispatch", "missing required")
    _rejects({**good_dispatch, "phase": "C"}, "dispatch", "bad enum")
    _rejects({**good_dispatch, "gpu_req": 3}, "dispatch", "wrong type")

    # --- Phase A ---
    good_a = {
        "unit_id": "u1", "status": "completed", "reproduced": True, "flaky": False,
        "pass_rate": 0.2, "reason_not_reproduced": None, "evidence_ref": "p/123",
        "root_causes": [{"culprit_symbol": "f.py:foo", "cause_category": "logic",
                         "mechanism": None, "error_signature": "AssertionError",
                         "observed_frequency": 1.0, "signature": "abcd"}],
    }
    contracts.validate_named(good_a, "phase_a")
    _rejects({**good_a, "status": "weird"}, "phase_a", "bad status enum")
    _rejects({**good_a, "reason_not_reproduced": "nope"}, "phase_a", "bad reason enum")
    bad_rc = {**good_a, "root_causes": [{"cause_category": "logic", "signature": "x"}]}
    _rejects(bad_rc, "phase_a", "root_cause missing culprit_symbol")
    bad_rc2 = {**good_a, "root_causes": [{"culprit_symbol": "a", "cause_category": "b",
                                          "signature": "s", "junk": 1}]}
    _rejects(bad_rc2, "phase_a", "root_cause extra key")

    # --- Phase B ---
    good_b = {"unit_id": "u1", "status": "completed", "diff_url": "D123",
              "local_commit_sha": "abc", "ci_status": "local_green"}
    contracts.validate_named(good_b, "phase_b")
    _rejects({**good_b, "ci_status": "green"}, "phase_b", "bad ci_status enum")
    _rejects({**good_b, "status": None}, "phase_b", "status must be string")

    print("PASS test_contracts: dispatch + phase_a + phase_b accept/reject correct")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
