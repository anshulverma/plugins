# Knowledge entry schema (the quality bar)

Every entry in `${INVESTIGATIONS_DIR}/knowledge/<slug>.md` (default set in `scripts/kb.py`) describes one *learning*: a confirmed root-cause + fix + prevention, from either our own investigation or an ingested external source (RCA, incident review, postmortem, post, doc). Entries are the reusable memory `/dexter:investigate` looks up before starting.

Read ~/.claude/LOCAL.md if present for local names and details.

**Generic text only.** The knowledge base lives in a git repo, so an entry describes each system by its role ("the cluster job scheduler", "the blob store") and keeps public names (open-source tools, public error strings, metric and config names) unchanged. Real identifiers — incident, diff, task, paste and job ids, URLs, internal system names, internal error strings, people, absolute home/data paths — go in the private refs file `$DEXTER_HOME/refs/<id>.txt` (`DEXTER_HOME` defaults to `~/.dexter`), which `kb.py search` reads alongside the entry and which is never committed.

**Non-negotiable: no holes.** An entry is only accepted when every field below is filled with real, specific content. Vague, placeholder (`TBD`/`TODO`/`?`), or evidence-free entries are worse than none — they mislead future investigations. `kb.py validate` enforces this; `/dexter:learn` must keep fetching from the source until the entry passes.

## Format

YAML frontmatter + required body sections:

```markdown
---
id: <kebab-slug>                     # unique, stable
title: <one concrete sentence>
date: <YYYY-MM-DD>
goal: <what the investigation set out to achieve, incl. definition-of-done>
outcome: <goal-met | partial | handed-off | understand-only> — <one-line before->after result or what remains>
source: own_investigation | external
source_refs:                         # REQUIRED, >=1 — generic description of each source
  - <e.g. team incident review, 2026-05; design doc; public article. Real URLs and ids go in $DEXTER_HOME/refs/<id>.txt>
environment:                         # REQUIRED — WHERE the issue + investigation happened (no holes)
  org: <work | personal | external>
  surface: <monorepo | cluster job | dev server | laptop | prod-service | CI | ...>
  hardware: <e.g. 32xH100 | dev-server-96cpu | MacBook M3 | N/A>
  workload: <e.g. MoE SFT training | RPC service | data pipeline | CLI | web app>
  stack: <e.g. PyTorch/FSDP | python/Buck2 | ...>
domain: <short area, e.g. gpu-training-perf | dataloader | thrift | build | memory>
tags: [<lookup keywords>]
confidence: <0.0-1.0>
status: confirmed | provisional
---

## Symptom
What was observed, with concrete data points (numbers, metrics, error strings). Not "it was slow" — "step_time ~85s at ~1.5% MFU, SM util p50 12.7%".

## Root cause
The confirmed mechanism, with the evidence chain that established it. State what it is AND why the alternatives were excluded.

## Fix
What was actually changed, described generically (the change ids go in the refs file). If from an external source, what they did.

## Prevention
How reoccurrence is avoided going forward (postmortem-style): guardrails, alerts, tests, lint/CI rules, config defaults, docs. If none exists, say so and name the gap.

## Data points
Concrete numbers/metrics that anchor this entry (>=1). Before/after, thresholds, sizes, timings.

## Generalizable lesson
What a *future, possibly different* investigation should take from this — the transferable heuristic, not just the specific fix. This is what makes the entry pay off later.

## Verification
How the root cause and fix were confirmed (tests, measurements, A/Bs, reproduction). For external entries, how the source validated it.
```

## Acceptance checklist (what `kb.py validate` checks)

- [ ] All frontmatter keys present and non-empty; `environment` has all five sub-keys filled.
- [ ] `source_refs` has >= 1 generic source description.
- [ ] `environment.org` is `work`, `personal` or `external`.
- [ ] No private identifier, URL, absolute home/data path, email or denylist term (`$DEXTER_HOME/denylist.txt`) in the content or the filename; the failure says to move it to `$DEXTER_HOME/refs/<id>.txt`. `kb.py scan` runs the same check over every file in the investigations repo.
- [ ] All seven body sections present and non-trivial (each more than a stub).
- [ ] `## Data points` contains >= 1 concrete number/metric.
- [ ] No placeholder tokens (`TBD`, `TODO`, `???`, `N/A` outside `environment.hardware`).
- [ ] `## Symptom` and `## Root cause` cite specific evidence, not adjectives.

## Why environment is required

The same symptom has different causes in different contexts (a 106B-MoE on 32×H100 vs a service on a dev server). Future lookups filter by `environment` + `domain` + `tags`, so an entry with a fuzzy environment is nearly unusable. Always record: which org (work / personal / external), which surface, what hardware, what workload, what stack.
