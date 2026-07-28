# Hardening Report: Hermes engine-core spec + plan

Date: 2026-07-28. Mode: `auto-plan --harden --max-passes 10` over the two-document
artifact set (`engine-core.md` + `engine-core-plan.md`), with `DESIGN.md` as a
frozen constraint.

## Result

**CONVERGED after 8 passes** (budget was 10). Each pass = a fresh-context
adversarial review-and-fix over BOTH docs at once (its unique value: catching
spec↔plan and spec↔DESIGN inconsistencies), followed by an independent
convergence judge that re-diffed both snapshots and hunted for new gaps.
Convergence confirmed by a zero-edit pass 8 (byte-identical diff, no UNRESOLVED).

## Convergence (instability = material + gaps + pending + unresolved)

```
18 ██████████████████
13 █████████████
 4 ████
10 ██████████        ← parked-state cluster surfaced late (pass 4)
 5 █████
 1 █
 1 █
 0                    ← converged (pass 8)
```

(`2026-07-28-engine-core-convergence.csv` has the component breakdown. The pass-4
bump reflects the parked-state/lease-capacity cluster that earlier passes missed.)

## What the loop found and fixed (highlights)

| Pass | Material fixes |
|------|----------------|
| 1 | Missing envelope `payload` field; `done_contract` naming + `Issue`/`IssueQuery` shape aligned to DESIGN; **entire run-state machine** added; built-but-untested `ssh_transport` given a test. |
| 2 | Run control had no setter → added `set_run_state` + `hermes run pause/resume/stop`; reduction accept/reject + `needs_human` resolution built by no slice → added callables + `hermes reduction accept/reject` + `reduction_id` linkage; uniform `reducing` gate; `payload_sha256` compute/verify assigned + tamper test. |
| 3 | **FK type bug**: `tickets.reduction_id TEXT` vs `reductions.id INTEGER` (would silently break under `foreign_keys=ON`) → `INTEGER`; `paused` freezes ALL progression (not just dispatch); phase-advancement gate tightened to avoid orphaning requeued `parked`/`needs_human` tickets. |
| 4 | **Parked/lease cluster**: no capacity source, no lease-release-on-completion, no park/unpark callables → capacity = Σ per-host resources; `release` on completion; `park_ticket`/`unpark_ready`; `reclaim_expired` requeues only non-terminal. |
| 5 | Lease `release` now fires on **every** `running` exit (incl. infra-retry + transport requeue), not just success paths; Slice 8 gains the `unpark_ready` deliverable + re-admit test. |
| 6 | **AC2 was unsatisfiable**: `hermes run` only started `master_loop` (which never claims/executes) → now co-launches in-process serve loops for local hosts. |
| 7 | `record_result` signature lacked `playbook`/`site` needed to evaluate the `verify` gate it applies → added. |
| 8 | (none — clean confirmation) |

## Decisions made by default (worth a glance; all reversible)

1. **Auto run→failed on a genuinely stuck run** (no actionable tickets,
   `next_phase==None`, `is_done` False) — prevents silent hangs; deterministically
   testable.
2. **Uniform `reducing` gate** — every `ok` ticket passes through `reducing`
   (a no-op `reduce` returns `[]` → straight to `done`). Lets a final-phase
   reduction gate human sign-off.

## Cross-sub-project item to reconcile later (NOT a blocker)

- **`hermes serve` verb collision**: engine-core §10 uses `hermes serve --host`
  for the per-host worker loop; `DESIGN.md` §10 uses `hermes serve` for the
  sub-project-3 FastAPI control-plane server (which mints `api_token`).
  Recommendation (deferred to sub-project 3): name the API server
  `hermes serve --api` / `hermes serve-api` and keep `hermes serve --host` for
  the worker loop.

## Artifacts

- Hardened spec: `hermes/docs/specs/engine-core.md`
- Hardened plan: `hermes/docs/specs/engine-core-plan.md`
- Convergence data: `2026-07-28-engine-core-convergence.csv`; this report.

Per-pass snapshots were transient; the per-pass tables above are the audit trail.
