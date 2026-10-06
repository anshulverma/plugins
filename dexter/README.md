# dexter

A forensic investigator for any bug, outage/SEV, RCA, performance mystery, flaky test, or regression. It runs controlled experiments to localize a root cause with high confidence — and it accumulates a structured, reusable knowledge base so every future investigation starts smarter.

Named after the forensic analyst who solves cases by evidence, not hunches.

## Commands

- **`/dexter:solve <goal>`** — solve it end-to-end: state the goal + definition-of-done, unique ID + durable audit trail, look up prior knowledge, frozen deep-researched baseline (incl. *where it happened*), a per-investigation knowledge graph, multi-test hypothesis verdicts (nothing dangling, parts must reconcile to the whole), localize the cause (a milestone), then **drive through to a verified fix** — measured against baseline — and bank the learning. Reports `IN PROGRESS` until the goal is met, not just when the cause is found.
- **`/dexter:investigate <question>`** — the same loop, **understand-only**: everything solve does up to and including the localized cause (Phases 0-4 + bank the learning), and none of the fix (Phase 5). Ends at `results/CONCLUSION.md` with the evidence chain, what was excluded, and a *recommended* fix it deliberately does not implement. Reports `LOCALIZED`, or an honest `NOT LOCALIZED` naming the next discriminating experiment. Use it when the diagnosis is the deliverable, when the fix is someone else's call, or when the work must stay read-only — e.g. a fleet of workers examining changes they must not touch.
- **`/dexter:learn <url | Pxxxx | SEV | DERP | Dxxxx | text>`** — ingest someone else's investigation/RCA and distil it into a validated, structured knowledge entry (symptom → root cause → fix → prevention → environment → generalizable lesson), with no holes.

## Where things live

- **Plugin code** (this dir): `commands/`, `references/` (`METHODOLOGY.md`, `KNOWLEDGE-SCHEMA.md`, `HYPOTHESIS-TEMPLATE.md`), `scripts/` (`new_investigation.sh`, `log.sh`, `record_job.sh`, `kg.py`, `kb.py`).
- **Knowledge base + investigations** (persistent, dotsync-safe): `~/workspace/investigations/`
  - `knowledge/<slug>.md` — structured learnings (schema-enforced, no holes)
  - `KNOWLEDGE.md` — index
  - `LESSONS.md` — accumulated process/methodology lessons (full text)
  - `LESSONS-DIGEST.md` — one line per lesson (`<id> <trigger> -> <action>`), the compact context Dexter loads instead of `LESSONS.md`
  - `cases/<id>/` — the raw per-investigation working dirs

## Lessons and writing

Dexter reads `LESSONS-DIGEST.md` at bootstrap and pulls full lessons with `kb.py lesson <id>` only when a line matches. It re-runs `kb.py search` (knowledge entries plus lessons) before each experiment, before concluding, and before designing a fix, and journals which lessons it applied. When it adds a lesson, `kb.py digest` fails until the new lesson has its digest line. Reports, knowledge entries and new lessons go through the `humanize` skill before they are final, with their required structure and searchable identifiers kept intact (`references/METHODOLOGY.md`, "Writing for readers").

## The knowledge quality bar

Every knowledge entry must fill: `source_refs` (>=1), `environment` (org / surface / hardware / workload / stack), symptom (with data points), root cause (with evidence), fix, prevention, data points (>=1 number), generalizable lesson, verification. `kb.py validate` rejects holes and placeholders. See `references/KNOWLEDGE-SCHEMA.md`.

## Scripts

```bash
INV=~/workspace/investigations/cases/<id>
scripts/new_investigation.sh <slug>                 # generate id + scaffold
scripts/log.sh   $INV "<message>"                    # journal
scripts/record_job.sh $INV <job> <hyp> "<env>" "<config>" "<result>"
python3 scripts/kg.py $INV node|edge|verdict|render|show ...   # per-investigation graph
python3 scripts/kb.py validate|search|index|template ...       # cross-investigation KB; search also ranks LESSONS.md
python3 scripts/kb.py lesson <id...> | digest                  # full lesson by id | check LESSONS-DIGEST.md covers every lesson
```
