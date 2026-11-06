#!/usr/bin/env python3
"""Slice 8 test: L0 dry-run reaches GO on the happy path and NO-GO on a contract
mismatch. `python3 test_dryrun.py`."""
from __future__ import annotations

import os
import tempfile

import dryrun


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="tfh_dryrun_")

    good = dryrun.l0(os.path.join(tmp, "good.db"))
    assert good["go"] is True, good
    for k in ("contract_roundtrip", "queue_transitions", "dedup", "land_guard", "diff_not_landed"):
        assert good["checks"][k] is True, (k, good["checks"])

    # A contract-violating worker forces a hard NO-GO.
    bad = dryrun.l0(os.path.join(tmp, "bad.db"), worker=dryrun._bad_worker)
    assert bad["go"] is False, bad
    assert bad["checks"]["contract_roundtrip"] is False
    assert "contract_error" in bad["checks"]

    print("PASS test_dryrun: L0 GO on happy path, hard NO-GO on contract mismatch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
