# Knowledge entry schema (the quality bar)

Every entry in `~/workspace/investigations/knowledge/<slug>.md` describes one *learning*: a confirmed root-cause + fix + prevention, from either our own investigation or an ingested external source (RCA, SEV, DERP, post, doc). Entries are the reusable memory `/dexter:investigate` looks up before starting.

**Non-negotiable: no holes.** An entry is only accepted when every field below is filled with real, specific content. Vague, placeholder (`TBD`/`TODO`/`?`), or evidence-free entries are worse than none — they mislead future investigations. `kb.py validate` enforces this; `/dexter:learn` must keep fetching from the source until the entry passes.

## Format

YAML frontmatter + required body sections:

```markdown
---
id: <kebab-slug>                     # unique, stable
title: <one concrete sentence>
date: <YYYY-MM-DD>
source: own_investigation | external
source_refs:                         # REQUIRED, >=1 — where this knowledge comes from / can be verified
  - <url or id: paste Pxxxx, GDoc, SEV Sxxxx, DERP, diff Dxxxx, task Txxxx, wiki, workplace post, article URL>
environment:                         # REQUIRED — WHERE the issue + investigation happened (no holes)
  org: <Meta | personal | other-org-name>
  surface: <fbsource | MAST | devserver | laptop | prod-service | CI | ...>
  hardware: <e.g. 32xH100 (och) | devserver-96cpu | MacBook M3 | N/A>
  workload: <e.g. MoE SFT training | Thrift service | data pipeline | CLI | web app>
  stack: <e.g. MSL/Ginger/morpheus | www/Hack | python/Buck | ...>
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
What was actually changed (link diffs/configs). If from an external source, what they did.

## Prevention
How reoccurrence is avoided going forward (DERP-style): guardrails, alerts, tests, lint/CI rules, config defaults, docs. If none exists, say so and name the gap.

## Data points
Concrete numbers/metrics that anchor this entry (>=1). Before/after, thresholds, sizes, timings.

## Generalizable lesson
What a *future, possibly different* investigation should take from this — the transferable heuristic, not just the specific fix. This is what makes the entry pay off later.

## Verification
How the root cause and fix were confirmed (tests, measurements, A/Bs, reproduction). For external entries, how the source validated it.
```

## Acceptance checklist (what `kb.py validate` checks)

- [ ] All frontmatter keys present and non-empty; `environment` has all five sub-keys filled.
- [ ] `source_refs` has >= 1 concrete reference (URL or Meta ID).
- [ ] All seven body sections present and non-trivial (each more than a stub).
- [ ] `## Data points` contains >= 1 concrete number/metric.
- [ ] No placeholder tokens (`TBD`, `TODO`, `???`, `N/A` outside `environment.hardware`).
- [ ] `## Symptom` and `## Root cause` cite specific evidence, not adjectives.

## Why environment is required

The same symptom has different causes in different contexts (a 106B-MoE on 32×H100 vs a service on a devserver). Future lookups filter by `environment` + `domain` + `tags`, so an entry with a fuzzy environment is nearly unusable. Always record: which org (Meta / personal), which surface, what hardware, what workload, what stack.
