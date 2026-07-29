# Spec-driven development — the pipeline

This is the repeatable playbook for taking a spec (or a spec idea) to shipped,
reviewed, tested code. It **composes existing skills** — it does not replace them:

- **auto-plan** — the convergence hardening loop (used on both the spec and the plan).
- **superpowers:subagent-driven-development** — fresh implementer per slice + per-slice review.
- **superpowers:test-driven-development** — the RED→GREEN discipline every implementer follows.

Invoke each via the Skill tool at the step where this playbook calls for it. What this
playbook adds on top is the **sequence** (spec→harden→plan→harden→execute), the
**resumable ledger**, **review adjudication**, and **coherence checkpoints**.

## When to use

A non-trivial unit of work that has (or deserves) a written spec: a subsystem, a
sub-project, a migration, a feature with real surface area. For a one-file change,
just use TDD directly — this pipeline's ceremony is not worth it.

## Inputs

- A **spec file** (`docs/specs/<name>.md`), or a **spec idea** to draft one from.
- The **target repo/dir** (default: cwd).
- Optional ledger path (`SDD_LEDGER`, default `.sdd-progress.md`, gitignored).

## The pipeline (phases)

```
scope+resume → spec → HARDEN spec → write plan → HARDEN plan → execute slices → coherence → finish
                        (converge)                 (converge)   (per-slice loop)   (checkpoint)
```

### 0. Scope + resume

- Identify the target repo + spec. Read the repo's conventions (its existing
  `docs/specs/*-plan.md`, test runner, structure) so everything you produce matches.
- **Check the ledger first.** Conversation memory does not survive compaction; the
  ledger does. Tasks marked complete there are DONE — do not redo them. Resume at the
  first unfinished step and trust the ledger + `git log` over recollection.
- **Branch convention.** Do not start implementation on the default branch without
  the user's consent. If they've said "develop on main," honor that; otherwise branch.

### 1. Spec

- If a spec doc exists, use it. If the input is an idea, draft `docs/specs/<name>.md`
  **grounded in the actual codebase** — read the real interfaces/signatures it must
  fit. Never spec against an idealized version of the system.

### 2. Harden the spec to convergence (auto-plan)

- Run hardening passes. Each pass scans for issues and **edits the spec to fix them**;
  scope passes per concern (or per document section) rather than one monolithic pass —
  a single agent trying to rewrite everything at once stalls.
- Continue until a pass makes **no material change** (converged). Cap the passes
  (e.g. ≤10) and report the count + whether it converged.
- Check against the **real code**: every interface/method the spec references must
  exist or be specified as an explicit, labeled **DELTA** (new flag/method/endpoint)
  with exact semantics. Do not invent features the engine lacks and pretend they're
  there. Nail concrete schemas/signatures; kill placeholders/TODOs.
- Commit.

### 3. Write the implementation plan

- `docs/specs/<name>-plan.md`, matching the repo's existing plan style. It must have:
  - **Global Constraints** section (invariants that bind every slice).
  - **Small, independently-testable, dependency-ordered slices** — deltas first, then
    the layers that depend on them, then the integration/acceptance slice last.
  - Each slice: scope · files · **the failing tests to write FIRST** (what they assert,
    against what real fixture) · DoD.
- Commit.

### 4. Harden the plan to convergence (auto-plan)

Same convergence loop, focused on:
- **Signature accuracy** — every engine call a slice makes matches the real code.
- **Slice independence + ordering** — no slice depends on a later one; delta/registration
  slices are sequenced after the modules they import exist.
- **Tests-first concreteness** — each slice names specific tests, not "add tests"; the
  integration slice genuinely proves the acceptance criteria (not a tautology).
- **Schemas nailed** and **risk mitigations concrete** (or flagged as accepted
  limitations with rationale — no hand-waving).
- Commit.

### 5. Execution loop (subagent-driven-development + TDD)

Invoke **superpowers:subagent-driven-development**; per slice:

1. **Extract** the slice's text from the plan into a uniquely-named **brief file**
   (`/tmp/.../<name>-<slice>-brief.md`) — do not make the implementer read the whole plan.
2. **Dispatch a fresh implementer subagent** (never reuse context across slices). The
   dispatch carries: one line on where the slice fits; the brief path ("read this first —
   your requirements, exact values verbatim"); interfaces/decisions from earlier slices
   the brief can't know; your resolution of any ambiguity; the report-file path. The
   implementer follows TDD (write failing tests, watch RED, minimal code to GREEN,
   refactor), runs the suite, commits, and writes a full report to its report file —
   returning only status + commits + a one-line test summary + concerns.
3. **On DONE, package the diff to a file** — commit list + `--stat` + `-U8` diff for the
   slice's commit range (BASE = the commit recorded before dispatch, never `parents(.)`).
4. **Dispatch a task reviewer** (spec compliance THEN code quality) with three file paths:
   the brief, the report, the diff — plus the binding global constraints copied verbatim as
   the reviewer's attention lens. Scale the reviewer's model to the diff's size/risk.
5. **Fix loop** — dispatch ONE fix subagent for the Critical/Important findings (name the
   covering tests; the fixer re-runs them and reports results); re-review until spec ✅ and
   quality approved. Record Minor findings in the ledger for the final triage.
6. **Ledger** the slice: append one line —
   `Slice N: complete (commits <base7>..<head7>, review clean) — <one-line what>`.

**File handoffs, always.** Everything you paste into a dispatch and everything a subagent
prints back stays resident in your context and is re-read every turn. Move briefs, reports,
and diffs as files; keep exact values (numbers, signatures, magic strings) only in the brief.

**Model selection.** Cheapest tier that can do the task: transcription-from-a-complete-spec
and single-file mechanical work → cheapest; multi-file integration → mid; architecture/final
review → most capable. Turn count beats token price — a too-weak model that takes 3× the
turns costs more. Always set the model explicitly.

### 6. Coherence checkpoints

At sensible intervals — and after the last slice — run an **architecture-coherence review**
of the whole (a capable-model subagent, read-only): protocol/pattern consistency across new
code and its siblings, no drift/duplication of load-bearing logic, consistent vocabulary,
clean layering, no dead code, honest invariants. Fix the cheap high-value items now; record
the larger refactors as **tracked follow-ups** in the ledger (never a silent discard). This
is the step that keeps a growing system feeling like one well-designed tool, not bolted-on
parts.

### 7. Finish

- Full test suite + build green; working tree pristine.
- Record the sub-project **complete** in the ledger.
- Report what shipped, the tracked follow-ups, and that **pushes remain manual** unless the
  user asked otherwise.

## Review adjudication (do not churn on false positives)

A reviewer runs with less context than you. Before dispatching a fix, adjudicate:

- If a finding conflicts with what you can prove from cross-file context (an invariant
  upstream already guarantees; a value that can't occur), **adjudicate it a non-defect** and
  record the evidence in the ledger — do not spin a fix cycle.
- If a finding **conflicts with what the plan mandates**, that is the human's call: present
  the finding beside the plan text and ask which governs.
- Otherwise, fix Critical/Important; ledger Minor for final triage.

Never instruct a reviewer to ignore or downrank an issue — let it raise the finding and
adjudicate it here.

## Invariants (bind every phase)

- Develop under the repo's branch convention; **commit as you go**; pushes manual unless asked.
- **Never fake test data, mocks-as-behavior, or tautological assertions** — drive real
  writers against real state; the only doubles are honest, injected test doubles.
- **Match the real interfaces** — unmet needs are explicit deltas, not pretend features.
- **Keep code self-contained — no doc references in code.** Comments, docstrings, SQL
  comments, and string descriptions must NOT cite the spec/plan docs: no `§N` section
  markers, no `Slice N`/"later slices", no `docs/…`/`*-plan.md` paths, no "per the
  spec/plan". Those citations live only in the docs (which are AI-execution artifacts and
  may be deleted). Code-to-code references (module/function/file names) are fine. Put this
  rule in every implementer's brief and have the reviewer flag any doc reference in code.
- A slice is done only when its review passes on **both** spec compliance and code quality.
- The ledger is the source of truth across compaction — write to it in the same turn you
  finish a step; trust it and `git log` over memory on resume.

## Anti-patterns

- One monolithic hardening pass over many docs → stalls. Scope per concern/document.
- Pasting prior-slice summaries into later dispatches → context bloat. Fresh brief only.
- Reviewing on the session's most expensive model regardless of diff size → wasteful.
- "It's done" at the spec/plan, or at root-cause of a slice, without the suite green.
- Silent truncation/deferral → always `log` what was dropped and why (ledger it).
