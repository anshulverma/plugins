---
name: test-fix-harness
description: Use to understand, debug, and fix a large set of failing/flaky/skipping tests fast by fanning work across on-demand devserver workers running headless Claude Code, coordinated by a master-owned SQLite queue over SSH. Two-phase diagnose->dedup->fix pipeline with testx-debug, a GPU/RE lease scheduler, and no-auto-land. Trigger for "fix all my failing tests", "distributed test fixing", "fan out test debugging across machines".
---

# Distributed Test-Fix Harness

You are the **master**. You own all state and run the judgment steps; the
**control-plane daemon** does mechanical SSH dispatch; **OD devserver workers**
run headless Claude Code to diagnose and fix, and can never land.

## Invariants (do not violate)

1. Master owns `queue.db` (local SQLite, WAL, mode 0600, never EdenFS).
2. Workers are stateless, reached only via master-initiated SSH. No peer-to-peer,
   no shared DB, no shared FS.
3. **Nothing auto-lands.** Workers stop at a published `jf submit` diff; a human
   lands. Enforce by construction (submit-only identity + PATH land-shims), not
   by prompt trust.
4. Reasoning runs on CPU; GPU/RE leases are acquired just-in-time and are scarce.
   Multi-GPU/NCCL is RE-only behind a small semaphore; overflow parks.
5. Every test-execution outcome (fail/pass/flaky/not-reproduced) is terminal.
   Retries increment only on infra failure (max 3).

## Workflow

1. **Preflight** — confirm the UNRESOLVED environment facts (RE quota, headless
   auth primitive, Claude installer command, testx history API, Phabricator bot
   identity). Choose one stable pinned trunk base commit for the whole run.
2. **Phase 0 (you)** — `triage.py seed` from `meta testinfra.issue list
   --owner-is=<owner> --state-is=OPEN -o json`; classify + prioritize + select 3
   dry-run canaries.
3. **Provision** — `bootstrap_worker.sh <host> <pinned>` per OD host;
   `verify_worker.sh` gates `hosts.txt`. Proceed above `MIN_WORKERS`.
4. **Dry-run gate** — L0 mock (deterministic, <2 min) then L1 real (3 canaries,
   full A->reduce->B). Contract mismatch = hard NO-GO.
5. **Phase A** — daemon dispatches diagnosis units; workers run `testx-debug` and
   return strict-schema root-cause reports.
6. **Reduce (you)** — dedup reports into `root_causes` (deterministic fingerprint
   merge + semantic adjudication); emit a diffable cluster report for human
   review before Phase B.
7. **Phase B** — one fix per cause; self-fix loop to green; `jf submit`; park red
   diffs for `ci_review`. Master re-verifies green via CI, never trusts worker
   claims.
8. **Wrap-up** — every unit reaches a terminal state; human reviews the cluster
   report and lands published diffs; parked GPU units are surfaced explicitly.

## Observability

Pull-based: `dispatcher.py status [--watch] | show <issue_id> | health |
sql "…"`. ATTENTION banners on parked_ratio>0.5, all-workers-down,
no_progress>30min, RE overflow.

## References

Design spec, plan, and ADRs 0001-0008 under `~/.claude/docs/`. Scripts and exact
schemas under this plugin's `scripts/`.
