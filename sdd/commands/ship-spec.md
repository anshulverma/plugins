---
description: Take a spec — or a spec idea — all the way to shipped, reviewed code. Hardens the spec to convergence (auto-plan), writes and hardens a TDD implementation plan, then executes it slice-by-slice via fresh implementer subagents with per-slice spec+quality review, fix loops, a resumable progress ledger, and periodic architecture-coherence checkpoints. Not complete until every slice is implemented, reviewed, and the full test suite is green.
argument-hint: <spec file path, or a description of the spec to build> [target repo/dir]
---

You are running the **spec-driven development pipeline**: turn the target below into shipped, reviewed, tested code by hardening the spec, hardening a plan, and executing it with subagents under TDD.

**Target:** $ARGUMENTS

Follow the methodology in `${CLAUDE_PLUGIN_ROOT}/references/PLAYBOOK.md` exactly — read it now. It defines convergence hardening, the per-slice execution loop, the ledger, review adjudication, and the coherence checkpoints. Compose the existing skills rather than re-deriving them: invoke **auto-plan** for the hardening loops, **superpowers:subagent-driven-development** for execution, and **superpowers:test-driven-development** throughout (invoke each via the Skill tool at the step where the PLAYBOOK calls for it).

Run these phases in order; do not skip:

1. **Scope + resume check.** Identify the target repo and the spec. Check for a `${SDD_LEDGER:-.sdd-progress.md}` ledger — anything marked complete there is DONE; resume at the first unfinished step, trusting the ledger + `git log` over memory. Confirm the branch convention with the user if unclear (do not start implementation on the default branch without consent).
2. **Spec.** If a spec doc exists, use it. If the target is an idea, draft `docs/specs/<name>.md` grounded in the ACTUAL codebase (read the real interfaces it must fit — never an idealized version).
3. **Harden the spec to convergence.** Run auto-plan hardening passes (scoped per concern), each editing the spec to fix drift/holes, until a pass makes no material change (converged) — cap the passes and report the count. Unmet needs the code lacks become explicit, labeled DELTAs, not invented features. Commit.
4. **Write the implementation plan** as `docs/specs/<name>-plan.md`: small, independently-testable, dependency-ordered TDD slices (deltas first); each slice names the failing tests to write first + its DoD; include a Global Constraints section. Match the repo's existing plan style. Commit.
5. **Harden the plan to convergence** the same way (signatures accurate vs the real code, slice independence + ordering, tests-first concreteness, schemas nailed, risk mitigations concrete). Commit.
6. **Execute the plan** via subagent-driven development + TDD, slice by slice (see PLAYBOOK "Execution loop"): extract each slice to a brief file → dispatch a fresh implementer subagent (TDD; cheapest capable model) → package the diff → dispatch a task reviewer (spec compliance AND code quality) → fix loop for Critical/Important findings (adjudicate false positives with cross-file evidence instead of churning) → append one line to the ledger. Hand artifacts over as files; keep the suite green after every slice; commit as you go.
7. **Coherence checkpoints.** At sensible intervals (and after the last slice), run an architecture-coherence review of the whole — protocol/pattern consistency, no drift/duplication, cohesion — and fix the cheap high-value items now, recording larger ones as tracked follow-ups.
8. **Finish.** Confirm the full test suite + build are green and the tree is pristine; record the sub-project complete in the ledger; report what shipped, the follow-ups, and that pushes remain manual unless the user asked otherwise.

Invariants (from PLAYBOOK): develop under the repo's branch convention and commit as you go; **never fake test data or assertions**; match the real interfaces (unmet needs are explicit deltas); scale each subagent's model to task complexity; a slice is done only when its review passes on BOTH spec compliance and code quality. Report at each phase boundary; never claim complete unless every slice is implemented, reviewed, and the suite is green.
