# test-fix-harness

Distributed harness to understand, debug, and fix a large set of failing / flaky
/ skipping tests fast, by fanning the work across on-demand dev host workers
that each run a headless Claude Code agent with local compute.

Read ~/.claude/LOCAL.md if present for local names and details. Local commands
(issue list, diff submit, format, lint, paste, test history, debug skill, repo
dir, owner) come from `TFH_*` environment variables documented there.

## Model

- **Master** (your box) owns all state in a single local SQLite file (`queue.db`)
  and runs two actors: a **control-plane daemon** (`dispatcher.py`, mechanical
  SSH dispatch) and **master Claude** (Phase-0 triage + dedup/reduce judgment).
- **Workers** are stateless on-demand dev hosts reached over SSH. They receive one unit
  + a strict JSON contract, return one JSON result, and **cannot land**.

## Pipeline

1. **Phase 0 — triage** (master Claude): seed the queue from the test
   platform's open issues (`$TFH_ISSUE_LIST_CMD`), classify each test (actionable / noise /
   likely-fixed / external), infer `gpu_topology` (hint), set priority,
   pre-cluster.
2. **Phase A — diagnose** (workers): reproduce + run the debug skill (`$TFH_DEBUG_SKILL`), emit a
   root-cause report. Reasoning runs on CPU; GPU is leased just-in-time.
3. **Reduce — dedup** (master Claude): cluster reports into unique root causes.
4. **Phase B — fix** (workers): one fix per cause, self-fix loop to green, then
   `$TFH_SUBMIT_CMD`. **Nothing auto-lands** — a human reviews and lands.

## GPU / RE

Capacity is partitioned and leased: multi-GPU/NCCL is RE-only behind a small
semaphore (overflow parked), single-GPU tries local first, CPU-mockable skips run
on CPU. See the design spec and ADRs.

## Commands

- `/fix-my-tests` — run (or resume) the pipeline for a test-issue dashboard.
- `/fix-my-tests:status` — pull-based dashboard over `queue.db`.

## Safety

Workers run `--dangerously-skip-permissions` (required for headless), made safe by
the no-land invariant and four independent land-blocks, worker attestation,
argv-not-shell allowlists, and CI re-verification of green claims. See
`ADR 0003` and `ADR 0005`.

## Status

Slices 1-9 implemented and tested (`scripts/run_tests.sh` -> ALL GREEN). Slice 10
(full run) needs real on-demand workers + RE quota and the daemon running in your login
shell, so it is not runnable from the agent sandbox.

`scripts/`:
- `schema.sql`, `migrate.py` - queue.db schema + idempotent migration runner (Slice 1)
- `triage.py` - Phase-0 triage seeder + canary selection (Slice 2)
- `contracts.py` - strict master<->worker JSON schemas + validator (Slice 3)
- `run_unit.sh`, `land_guard.sh` - worker runner + no-land shim (Slice 3)
- `../templates/phase_A.md`, `phase_B.md` - worker prompt templates (Slice 4)
- `dispatcher.py` - control-plane daemon core + observability CLI (Slices 5, 9)
- `scheduler.py` - GPU/RE lease scheduler (Slice 6)
- `reduce.py` - dedup / reduce step (Slice 7)
- `dryrun.py` - L0 mock GO/NO-GO gate (Slice 8)
- `bootstrap_worker.sh`, `verify_worker.sh`, `deprovision_worker.sh` - provisioning (Slice 9)
- `test_*.py`, `test_worker.sh`, `run_tests.sh` - test suite

Design: `~/.claude/docs/auto-plan/specs/2026-07-23-distributed-test-fix-harness-design.md`;
plan: `~/.claude/docs/auto-plan/plans/2026-07-23-distributed-test-fix-harness.md`.
