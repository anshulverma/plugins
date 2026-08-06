# dexter — investigation methodology

A disciplined loop for achieving a stated **goal** on *any* problem — a bug, an outage/SEV, a performance mystery, a flaky test, a regression — by empirically localizing the cause and then driving through to the goal. It exists to stop two failure modes: **flip-flopping between plausible fixes without ever proving where the problem is**, and **stopping at "we understand the cause" when the goal was to fix it.**

Seven non-negotiable properties:

1. **Goal-driven completion.** Every investigation starts from an explicit goal with a definition-of-done, and is NOT complete until that goal is met and verified. Finding the root cause is a *milestone*, not the finish line — if the goal was to fix/speed-up/stop something, "done" means the target metric or behavior measurably moved. An RCA with no verified fix is an investigation still in progress. Report `IN PROGRESS` until the goal is met (or the remaining work is explicitly handed off/blocked with the reason named).
2. **Durable audit trail.** Every test/experiment, code change, and result is logged under one unique investigation ID. Nothing lives only in chat.
3. **Cumulative knowledge graph** (per investigation). Findings accrete as an append-only graph of facts, hypotheses, experiments, and observations joined by `supports`/`refutes`/`motivates`/`depends_on` edges. Knowledge only grows.
4. **Multi-test hypothesis validation.** A hypothesis is `confirmed`/`refuted` only when **two or more independent tests agree AND the competing hypotheses are excluded.** No verdict from a single run; nothing left dangling. A *fix* is a hypothesis too — it is only confirmed when its measurement shows the goal metric moved.
5. **Static baseline first**, including **where it happened** (environment). Deep-research an immutable baseline before experimenting; freeze it.
6. **Cumulative knowledge base** (across investigations). Before starting, look up what we already learned; after finishing, write a structured, validated knowledge entry so the next investigation starts smarter.
7. **Fastest viable feedback loop.** Actively find the cheapest way to get each signal before spending on the expensive one. A slow loop is the biggest hidden tax on an investigation — shrinking it is a first-class goal, not an afterthought.

## Optimize the feedback loop (do this before every experiment)

Before running any experiment, explicitly answer: *what is the fastest way to get this signal?* Then use the cheapest tier that still exercises the mechanism. Record the tier you chose and why in the journal, so the cost/coverage tradeoff is auditable.

Cheapening tactics, in order of preference:
- **Run it locally instead of remote.** Can the component under test (dataloader, decode/fetch, tokenizer, a pure function, a query) run on a devserver/laptop rather than a remote/MAST/cluster job? A local repro is often minutes vs hours and avoids scheduling/queue latency. (In this repo: `rl/run.sh python <probe>.py` drove the dataloader locally at ~90s/iter vs ~2h/MAST-run.)
- **Reuse what's already running.** Trigger profiling/inspection on a live job instead of launching a new one; read an existing trace/log/metric before producing a new one.
- **Shrink the job.** Preresolved/smaller configs, fewer steps (`max_steps`), sub-sampled data, `fast_dev_run`/dry-run, a single shard, warm/cached builds over cold, a profiling window instead of the whole run.
- **Isolate the unit.** Extract the suspect stage into a standalone script/benchmark you can iterate on in a tight loop, rather than exercising the whole system each time.
- **Parallelize.** Run independent measurements/arms concurrently (background jobs, parallel agents) so wall-clock is the slowest one, not the sum.
- **Escalate only when forced.** Move to the expensive/remote tier only when the cheap tier structurally cannot exercise the mechanism (e.g. you genuinely need the GPU/model/cluster for forward/backward/comms, or production scale/data).

**Representativeness check:** when you use a cheaper tier, confirm it stands in for the real one by matching at least one shared metric to a real run (e.g. local per-image fetch 807ms ≈ GPU-worker 864ms). An unrepresentative cheap loop is worse than a slow correct one.

Capture reusable cheap-repro tricks in `~/workspace/investigations/LESSONS.md` so the next investigation inherits a faster loop.

## Directory layout

- **Cases** (per-investigation working dirs): `~/workspace/investigations/cases/<INVESTIGATION_ID>/` (`INVESTIGATION_ID = <slug>-<YYYYMMDD-HHMMSS>`, carried by every job name and artifact).
- **Knowledge base** (cross-investigation learnings): `~/workspace/investigations/knowledge/<slug>.md` + `KNOWLEDGE.md` index. Schema: `references/KNOWLEDGE-SCHEMA.md`.

Case dir contents: `JOURNAL.md`, `baseline/BASELINE.md` (+ `raw/`), `hypotheses/`, `jobs/INDEX.tsv` + `<job>.md`, `code/<job>.diff`, `results/`, `knowledge-graph/graph.jsonl` (+ `graph.dot`, `STATUS.md`).

## Helper scripts (`${CLAUDE_PLUGIN_ROOT}/scripts/`)

Route all logging through them so nothing is missed. `INV=~/workspace/investigations/cases/<ID>`.
- `new_investigation.sh <slug>` — generate ID + scaffold under `~/workspace/investigations/cases/`.
- `log.sh <INV> "<msg>"` — append to `JOURNAL.md`.
- `record_job.sh <INV> <job_id> <hyp> "<env>" "<config>" "<result>"` — record a test job + snapshot code diff.
- `kg.py <INV> node|edge|verdict|render|show ...` — the per-investigation knowledge graph.
- `kb.py validate|search|index|template ...` — the cross-investigation knowledge base.

## The loop

### Phase 0 — Goal + bootstrap
`new_investigation.sh <slug>`; record the ID. **State the GOAL and its definition-of-done first**, and `log.sh` it as a `goal` node. The goal is what "done" means — and it is usually NOT "find the cause." If the ask is to fix a bug / speed something up / stop an outage, the definition-of-done is a **verified fix** (the target metric or behavior actually moved, measured), not just the root cause. Write the goal as a testable target, e.g. "raise training MFU from ~1.5% toward the double-digit floor, verified on the same config," not "understand why MFU is low." Also note the **environment up front**: org (Meta/personal), surface (fbsource/MAST/devserver/prod/CI), hardware, workload, stack — required in the final knowledge entry.

### Phase 1 — Baseline (look up, then research, then FREEZE)
1. **Look up prior knowledge:** `kb.py search <symptom/domain/tags/env>`. Read any relevant entries — they may hand you the answer, the likely causes, or the cheap-repro trick. Cite them in the baseline.
2. **Deep-research** the unknowns (use `deep-research` or fan out agents): system under test; the exact code paths; **what each metric/signal actually measures** (read the emitting code); expected/healthy behavior (so you know the size of the gap); prior art (RCAs, SEVs, DERPs, posts, diffs).
3. Synthesize a curated `baseline/BASELINE.md` **including the environment block**, then **freeze it** (STATIC). Later contradictions are findings (graph nodes), not edits; changing the baseline needs the user's OK.

### Phase 2 — Frame question + candidate hypotheses
Write the question as a `question` node; enumerate candidate causes as `hypothesis` nodes (`--status open`), ideally mutually-exclusive/collectively-exhaustive so confirming one and refuting the rest localizes the cause. Seed with what the KB suggested.

### Phase 3 — Experiment loop (per open hypothesis)
1. **Design >= 2 discriminating tests** — each changes the answer only if the hypothesis is true; attack from different angles (direct measurement + control/ablation; A/B + an accounting check that must reconcile).
2. **Control variables** — one change per run, keep a baseline arm, repeat when a result could be noise.
3. **Test cheaply first** — reproduce at the lowest-cost tier that still exercises the mechanism (e.g. drive a dataloader locally before paying for a remote GPU job); escalate only when needed.
4. **Instrument opt-in** — behind a flag, default-off, so instrumentation never changes default behavior and every arm is comparable. **Emit a proof-of-life line** so a null result can't be confused with "the code never ran."
5. **Record everything** — `record_job.sh` per run; `kg.py` nodes/edges for experiments/observations.
6. **Verdict only at high confidence** — `confirmed`/`refuted` only when >=2 independent tests agree and competitors are excluded; else the hypothesis stays `open` (or `blocked`, naming the blocker). **Reconcile:** the measured parts must sum to the whole (e.g. phases sum to step_time); if they don't, you haven't localized it yet.

### Phase 4 — Localize (root cause milestone, NOT the finish)
When every hypothesis has a verdict and the accounting reconciles, write `results/CONCLUSION.md`: localized cause, evidence chain (job IDs), and what is now known *not* to be the cause. Render the final graph. **This is a milestone, not completion** — check the goal: if it was understand-only, go to Phase 6. Otherwise continue to Phase 5; the investigation stays `IN PROGRESS`.

### Phase 5 — Drive to the goal (fix + verify) — skip ONLY if the goal was understand-only

`/dexter:solve` runs this phase; `/dexter:investigate` is the same loop with this phase removed, and stops at the Phase 4 conclusion plus a *recommended* fix.
The fix is not "separate work" — it is the rest of *this* investigation, run with the same rigor.
1. **Design candidate fixes** from the confirmed cause; if several, rank by expected impact × cost and pick the highest-leverage one. Each candidate is a **hypothesis**: "change X will move goal-metric M by ~Y."
2. **Implement** the fix (cheapest feedback loop first — a local repro / small config / one knob before a full remote run).
3. **Verify** by measuring the goal metric on the same target and comparing to baseline. A fix is only `confirmed` when M actually moved in the right direction by a meaningful amount — not because it "should." A no-op or regression sends you back to step 1 with a new candidate.
4. **Iterate** until the goal's definition-of-done is met, or the remaining work is explicitly handed off (name who/what) or blocked (name the blocker). Record every attempt as a job + graph node, kept or discarded with its measurement.
5. Update `results/REPORT.md` to show the goal met (before → after numbers) or exactly what remains.

### Phase 6 — Bank the knowledge (mandatory)
1. Write a **structured knowledge entry** to `~/workspace/investigations/knowledge/<slug>.md` conforming to `KNOWLEDGE-SCHEMA.md` — including the **environment** block and the **verified fix** (before → after numbers). Run `kb.py validate <file>` and fix every gap (no holes), then `kb.py index`.
2. If you learned a reusable *process* technique (a cheaper repro, a metric gotcha), append it to `~/workspace/investigations/LESSONS.md`.

## Rigor rules (read every time)
- **The goal is the finish line, not the root cause.** An investigation is complete only when its definition-of-done is met and verified; a confirmed RCA with no verified fix is `IN PROGRESS`. Never present an RCA-only result as "done" when the goal was to fix something.
- One variable per run; always keep a control arm; a single run is never proof.
- Every claim cites a `job_id` or a baseline/KB reference.
- Metrics lie until you've read what they measure; reconcile averages against tails; separate cold-start from steady state.
- The baseline is immutable without user sign-off.
- Optimize nothing until the cause is *measured*; a plausible mechanism is a hypothesis, not a finding.
- Never add anything whose purpose is to game a detector/metric; fix the real cause.
