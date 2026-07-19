# dexter — investigation methodology

A disciplined loop for empirically localizing the root cause of *any* problem — a bug, an outage/SEV, a performance mystery, a flaky test, a regression — when reading the code is not enough and you must run experiments. It exists to stop the failure mode of **flip-flopping between plausible fixes without ever proving where the problem actually is.**

Five non-negotiable properties:

1. **Durable audit trail.** Every test/experiment, code change, and result is logged under one unique investigation ID. Nothing lives only in chat.
2. **Cumulative knowledge graph** (per investigation). Findings accrete as an append-only graph of facts, hypotheses, experiments, and observations joined by `supports`/`refutes`/`motivates`/`depends_on` edges. Knowledge only grows.
3. **Multi-test hypothesis validation.** A hypothesis is `confirmed`/`refuted` only when **two or more independent tests agree AND the competing hypotheses are excluded.** No verdict from a single run; nothing left dangling.
4. **Static baseline first**, including **where it happened** (environment). Deep-research an immutable baseline before experimenting; freeze it.
5. **Cumulative knowledge base** (across investigations). Before starting, look up what we already learned; after finishing, write a structured, validated knowledge entry so the next investigation starts smarter.

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

### Phase 0 — Bootstrap
`new_investigation.sh <slug>`; record the ID. `log.sh` the framing question. Note the **environment up front**: org (Meta/personal), surface (fbsource/MAST/devserver/prod/CI), hardware, workload, stack — this scopes everything and is required in the final knowledge entry.

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

### Phase 4 — Converge
When every hypothesis has a verdict and the accounting reconciles, write `results/CONCLUSION.md`: localized cause, evidence chain (job IDs), and what is now known *not* to be the cause. Render the final graph. Propose the fix as separate work, validated by re-running the same measurement.

### Phase 5 — Bank the knowledge (mandatory)
1. Write a **structured knowledge entry** to `~/workspace/investigations/knowledge/<slug>.md` conforming to `KNOWLEDGE-SCHEMA.md` — including the **environment** block. Run `kb.py validate <file>` and fix every gap (no holes), then `kb.py index`.
2. If you learned a reusable *process* technique (a cheaper repro, a metric gotcha), append it to `~/workspace/investigations/LESSONS.md`.

## Rigor rules (read every time)
- One variable per run; always keep a control arm; a single run is never proof.
- Every claim cites a `job_id` or a baseline/KB reference.
- Metrics lie until you've read what they measure; reconcile averages against tails; separate cold-start from steady state.
- The baseline is immutable without user sign-off.
- Optimize nothing until the cause is *measured*; a plausible mechanism is a hypothesis, not a finding.
- Never add anything whose purpose is to game a detector/metric; fix the real cause.
