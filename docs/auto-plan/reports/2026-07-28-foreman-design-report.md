# Hardening Report: Foreman design (`foreman/docs/DESIGN.md`)

Date: 2026-07-28. Mode: `auto-plan --harden --max-passes 10 --skip-plan` on an
existing spec.

## Result

**CONVERGED after 7 passes** (budget was 10). Each pass = a fresh-context
adversarial review-and-fix, followed by an independent convergence judge that
re-diffed the snapshot against the current doc and hunted for remaining gaps.
Convergence = a pass with **zero material changes** and zero open gaps, confirmed
by the judge diffing byte-identical files.

## Convergence

```
instability score per pass (material + gaps + pending + unresolved markers)

17 █████████████████
15 ███████████████
 7 ███████
 3 ███
 2 ██
 2 ██
 0                     ← converged (pass 7)
```

(`2026-07-28-foreman-design-convergence.csv` has the component breakdown.)

## What each pass changed

| Pass | Material fixes | Judge verdict |
|------|----------------|---------------|
| 1 | Unified `goal`/`driver`/`timeout` duplication in the contract; defined `failed`/`needs_human`/`parked` state semantics + retry-exhaustion; added heartbeat (30 s) + lease TTL (1800 s) defaults; closed both original open questions (FastAPI isolated to `server/`; `meta` site ships in-repo, `FOREMAN_SITE=meta`). | NOT CONVERGED (9 material) |
| 2 | Defined `Result`, `Check`, `IssueQuery`, `Issue` types; split driver-failure (terminal) vs infra-failure (retry ×3); made `guardrails` concrete (`{no_ship}`); dropped unenforceable `max_turns`; added localhost-bind + bearer-token API auth. | NOT CONVERGED (9 material) |
| 3 | Total `termination_reason → outcome → disposition` table (timeout ⇒ terminal); `Site.guarantees_no_ship()`; `review_state` enum; two-level no-ship enforcement; SPA token acquisition + token lifecycle; re-verify-failure ⇒ `needs_human`. | NOT CONVERGED (6 material) |
| 4 | Added reduction accept/reject control action (REST + events + `409`) and the `reductions` read endpoint. | NOT CONVERGED (2 material) |
| 5 | Defined `needs_human` exit transitions (accept ⇒ `done`, reject ⇒ `failed`) and reconciled the banner rule between §5 and §10. | NOT CONVERGED (2 material) |
| 6 | Added `Playbook.verify(...)` — the interface for the master-side independent re-verify relied on by §3/§8/§11. | NOT CONVERGED (2 material) |
| 7 | (none — clean confirmation pass) | **CONVERGED** |

## Decisions worth a human glance

Two items were resolved with a sensible default during hardening; both are
low-stakes and reversible, flagged here so you can confirm:

1. **Timeout is terminal, not retried** (pass 3). A ticket that blows `timeout_s`
   is classified `driver_failed` (no retry), on the logic that the same driver +
   input is unlikely to change. Tension: a timeout can also be transient host
   slowness. Documented future-reclassify path: if timeouts prove frequently
   infra-transient, switch to a single bounded retry-on-a-different-host.
2. **`runs.state` enum deferred to the engine-core spec** (pass 7). Every other
   state field is enumerated inline; run-level lifecycle (running/stopped/done and
   stopped-run dispatch behavior) is engine-core territory. The judge ruled this a
   legitimate umbrella-scope deferral, not a gap.

## Artifacts

- Hardened spec: `foreman/docs/DESIGN.md`
- Convergence data: `2026-07-28-foreman-design-convergence.csv`
- This report

Per-pass snapshots were transient; the per-pass change tables above are the audit
trail.
