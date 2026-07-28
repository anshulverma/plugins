---
description: Run (or resume) the distributed test-fix pipeline for a test-issue dashboard - triage, fan out diagnosis across OD workers, dedup root causes, fix one per cause, stop at published diffs (nothing lands).
argument-hint: "[owner or fburl dashboard] [--resume] [--dry-run-only]"
---

Run the distributed test-fix harness for: $ARGUMENTS

Load the `test-fix-harness` skill and follow it exactly. Do not skip the invariants.

Steps:
1. Resolve the target dashboard/owner (default: current user's open test issues).
2. Preflight the UNRESOLVED environment facts; pick one pinned stable trunk base.
3. Phase 0 triage -> seed queue.db, classify, prioritize, pick 3 canaries.
4. Provision OD workers (bootstrap + verify); proceed above MIN_WORKERS.
5. Dry-run gate: L0 mock then L1 real (3 canaries). Hard NO-GO on contract mismatch.
6. On GO: Phase A diagnose -> reduce/dedup (emit cluster report for human review)
   -> Phase B fix (jf submit only; nothing lands).
7. Report terminal state of every unit; surface parked-on-GPU units; hand the
   human the cluster report + published diffs to land.

If `--resume`, reconcile queue.db and continue from the last state.
If `--dry-run-only`, stop after the L1 GO/NO-GO report.
