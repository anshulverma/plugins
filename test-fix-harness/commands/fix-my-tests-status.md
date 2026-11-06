---
description: Pull-based dashboard for the distributed test-fix harness - unit/worker/GPU breakdowns, parked units by reason, and per-issue history, read from the master-owned queue.db.
argument-hint: "[--watch] [--parked [--reason gpu_capacity]] [show <issue_id>]"
---

Show the current state of the distributed test-fix harness for: $ARGUMENTS

This is a thin wrapper over the control-plane daemon's read-only status surface.
Run the appropriate `dispatcher.py` command and summarize:

- Default: `dispatcher.py status` — units total / pending / leased / done /
  failed / parked (by park reason), broken out by phase (A/B) and route
  (cpu / local_gpu / re_gpu); worker idle/busy/down; RE + local GPU semaphore
  slots used/free; any ATTENTION banners.
- `--watch`: `dispatcher.py status --watch` (redraws every 5s).
- `--parked [--reason <r>]`: `dispatcher.py status --parked [--reason <r>]`.
- `show <issue_id>`: `dispatcher.py show <issue_id>` (unit state + cluster +
  every runs attempt).

Never mutate state from this command. For raw queries use
`dispatcher.py sql "<SELECT ...>"`.
