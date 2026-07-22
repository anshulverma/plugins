# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Personal Claude Code plugins. Each top-level subdirectory is a **self-contained plugin**: a `.claude-plugin/plugin.json` manifest plus a `commands/` directory (and optionally `references/` and `scripts/`). Plugins are activated by symlinking each into `~/.claude/plugins/local/<name>`. There is no build step, package manager, or test framework — the "code" is Markdown command prompts plus small standalone helper scripts.

`install.sh` at the repo root activates everything at once: it symlinks every plugin (any dir with `.claude-plugin/plugin.json`) into `~/.claude/plugins/local/<name>`, then installs the statusline. It is idempotent and requires `jq`.

Currently one plugin: **dexter**. Plus one non-plugin asset: **statusline**.

## statusline (non-plugin)

`statusline/statusline-command.sh` is a Claude Code statusline, forked from [danielmackay/claude-code-statusline](https://github.com/danielmackay/claude-code-statusline). It is **not** a plugin (a statusline is configured through settings.json `statusLine`, not a `plugin.json`), so it is not symlinked into `plugins/local/`. Instead `install.sh` symlinks the script to `~/.claude/statusline-command.sh` and merges a `statusLine` block into `~/.claude/settings.json` via `jq` (existing keys preserved).

Local addition over upstream: a `🧩 i-have-adhd` badge on line 1, shown when the [i-have-adhd](https://github.com/ayghri/i-have-adhd) always-on flag `${CLAUDE_CONFIG_DIR:-~/.claude}/.i-have-adhd-always` exists. That flag file is the only persistent signal for the mode; a per-session `/i-have-adhd` invocation leaves nothing on disk, so it is intentionally not detected. The script reads its JSON status payload from stdin (`jq`) and appends optional badges (`💪` effort, `🧩` adhd) only when present, using positional `printf` args.

## Critical architecture: plugin code vs. runtime data

The single most important thing to understand is a hard separation between two locations:

- **Plugin code (this repo)** — the *how*: command prompts, methodology docs, schemas, and helper scripts. Version-controlled here. Referenced at runtime via `${CLAUDE_PLUGIN_ROOT}` (which resolves to the plugin's dir, e.g. `dexter/`).
- **Runtime data (`~/workspace/investigations/`)** — the *output*: per-investigation working dirs (`cases/<id>/`) and the cross-investigation knowledge base (`knowledge/<slug>.md`, `KNOWLEDGE.md`, `LESSONS.md`). This lives **outside the repo**, is persistent/dotsync-safe, and is written by the scripts. Override its root with `INVESTIGATIONS_DIR` (for `kb.py`) or `INV_BASE` (for `new_investigation.sh`).

When editing dexter, do not confuse the two: commands orchestrate the methodology and call the scripts; the scripts own all reads/writes to the runtime data. Never hardcode `~/workspace/investigations` into new command prompts — go through the scripts or the env vars.

## dexter plugin

A forensic investigator that localizes a root cause by controlled experiment, drives through to a *verified fix*, and banks a reusable, schema-validated learning. Two commands:

- **`/dexter:solve <goal>`** (`dexter/commands/solve.md`) — the full 6-phase investigation loop.
- **`/dexter:learn <url|Pxxxx|SEV|DERP|Dxxxx|text>`** (`dexter/commands/learn.md`) — ingest an external RCA/SEV/postmortem into one validated knowledge entry.

Both commands are prompt files; the real specification lives in `dexter/references/`:
- `METHODOLOGY.md` — the seven non-negotiable properties and the phased loop. **This is the source of truth for how `/dexter:solve` behaves.** Edit behavior here, not just in the command file.
- `KNOWLEDGE-SCHEMA.md` — the required shape of a knowledge entry (the "no holes" quality bar).
- `HYPOTHESIS-TEMPLATE.md` — scaffold for a hypothesis.

### The two scripts and their invariants

`dexter/scripts/kg.py` — **append-only** per-investigation knowledge graph (`cases/<id>/knowledge-graph/graph.jsonl`). Every mutation is a new JSONL line; nothing is ever rewritten, so full history (including status changes) is preserved. Current state is *folded* from the log on read (`render`/`show`). Node types: `question|fact|hypothesis|experiment|observation`. Edge rels: `supports|refutes|motivates|depends_on|tests|answers`. A hypothesis only becomes `confirmed`/`refuted` via a `verdict`, which warns if given fewer than 2 independent job IDs (the multi-test rule).

`dexter/scripts/kb.py` — the cross-investigation knowledge base. `validate` enforces the no-holes schema (all frontmatter keys incl. all five `environment` sub-keys, `>=1` source ref, all seven body sections non-stub, `>=1` numeric data point, no placeholder tokens); it exits non-zero and lists gaps. Note: `kb.py` deliberately **has no third-party dependencies** — it ships its own minimal frontmatter parser rather than importing PyYAML. Keep it that way.

Shell helpers: `new_investigation.sh <slug>` (generate `<slug>-<YYYYMMDD-HHMMSS>` ID + scaffold the case dir), `log.sh` (append to `JOURNAL.md`), `record_job.sh` (record a test job + best-effort snapshot the working-copy diff via `sl` or `git`).

### Methodology invariants that must survive edits

These are the point of the plugin — preserve them when changing prompts or scripts:
- **Goal-driven completion** — done means the goal's definition-of-done is verified, not "root cause found." Finding the cause is a milestone; a confirmed RCA with no verified fix is still `IN PROGRESS`.
- **Multi-test verdicts** — no `confirmed`/`refuted` from a single run; `>=2` independent tests must agree and competitors be excluded.
- **Static baseline** — frozen before experimenting; later contradictions are graph nodes, not edits.
- **Fastest viable feedback loop** — pick the cheapest tier (local before remote) that still exercises the mechanism, with a representativeness check.
- **No holes** in banked knowledge — `kb.py validate` must pass before `kb.py index`.

## Working with scripts

No test suite. To sanity-check the Python scripts, exercise them against a temp runtime dir, e.g.:

```bash
# kb.py: validate a template against the schema
python3 dexter/scripts/kb.py template my-slug > /tmp/e.md   # NB: template has placeholders, so this is INVALID by design
INVESTIGATIONS_DIR=/tmp/inv python3 dexter/scripts/kb.py validate /tmp/e.md

# kg.py: scaffold a case and drive the graph
INV_BASE=/tmp/cases dexter/scripts/new_investigation.sh demo
INV=/tmp/cases/$(cat /tmp/cases/.current)
python3 dexter/scripts/kg.py "$INV" node --id H1 --type hypothesis --text "..."
python3 dexter/scripts/kg.py "$INV" show
```

Adding a plugin: create `<name>/.claude-plugin/plugin.json` (with a `commands` glob), a `commands/` dir, and symlink it into `~/.claude/plugins/local/<name>`.
