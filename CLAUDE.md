# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Personal Claude Code plugins. Each top-level subdirectory is a **self-contained plugin**: a `.claude-plugin/plugin.json` manifest plus a `commands/` directory (and optionally `references/` and `scripts/`). Plugins are activated by symlinking each into `~/.claude/plugins/local/<name>`. There is no build step, package manager, or test framework — the "code" is Markdown command prompts plus small standalone helper scripts.

`install.sh` at the repo root activates everything at once: it symlinks every plugin (any dir with `.claude-plugin/plugin.json`) into `~/.claude/plugins/local/<name>`, then installs the statusline. It is idempotent and requires `jq`.

Plugins: **dexter** (documented below), **hermes**, **sdd** and **test-fix-harness**. Plus one non-plugin asset: **statusline**.

## statusline (non-plugin)

`statusline/statusline-command.sh` is a Claude Code statusline, forked from [danielmackay/claude-code-statusline](https://github.com/danielmackay/claude-code-statusline). It is **not** a plugin (a statusline is configured through settings.json `statusLine`, not a `plugin.json`), so it is not symlinked into `plugins/local/`. Instead `install.sh` symlinks the script to `~/.claude/statusline-command.sh` and merges a `statusLine` block into `~/.claude/settings.json` via `jq` (existing keys preserved).

Local addition over upstream: a `🧩 i-have-adhd` badge on line 1, shown when the [i-have-adhd](https://github.com/ayghri/i-have-adhd) always-on flag `${CLAUDE_CONFIG_DIR:-~/.claude}/.i-have-adhd-always` exists. That flag file is the only persistent signal for the mode; a per-session `/i-have-adhd` invocation leaves nothing on disk, so it is intentionally not detected. The script reads its JSON status payload from stdin (`jq`) and appends optional badges (`💪` effort, `🧩` adhd) only when present, using positional `printf` args.

## Critical architecture: plugin code vs. runtime data

The single most important thing to understand is a hard separation between two locations:

- **Plugin code (this repo)** — the *how*: command prompts, methodology docs, schemas, and helper scripts. Version-controlled here. Referenced at runtime via `${CLAUDE_PLUGIN_ROOT}` (which resolves to the plugin's dir, e.g. `dexter/`).
- **Runtime data** — the *output*, **outside this repo**, written by the scripts, in two roots:
  - **Knowledge base (`${INVESTIGATIONS_DIR}`, default `~/workspace/investigations/`)** — its own git repo, generic text only: `knowledge/<slug>.md` entries, the `KNOWLEDGE.md` index, `LESSONS.md` and its one-line-per-lesson `LESSONS-DIGEST.md`.
  - **Private data (`$DEXTER_HOME`, default `~/.dexter/`; dirs 700, files 600; never in git)** — raw per-investigation working dirs (`cases/<id>/`, override with `INV_BASE`), the real identifiers behind each entry (`refs/<slug>.txt`, read by `kb.py search`), and `denylist.txt` (private terms, `term<TAB>cs|ci`, `re:` for a regex) that `kb.py validate`, `kb.py scan` and `record_job.sh` refuse.

When editing dexter, do not confuse the two: commands orchestrate the methodology and call the scripts; the scripts own all reads/writes to the runtime data. Never hardcode either root into new command prompts — go through the scripts or the env vars. Anything internal (system names, ids, URLs, people) goes to `$DEXTER_HOME`, never into the knowledge base.

Read ~/.claude/LOCAL.md if present for local names and details.

## dexter plugin

A forensic investigator that localizes a root cause by controlled experiment, drives through to a *verified fix*, and banks a reusable, schema-validated learning. Two commands:

- **`/dexter:solve <goal>`** (`dexter/commands/solve.md`) — the full 6-phase investigation loop.
- **`/dexter:investigate <question>`** (`dexter/commands/investigate.md`) — the same loop, understand-only: stops at the localized cause and a recommended fix.
- **`/dexter:learn <url | id | text>`** (`dexter/commands/learn.md`) — ingest an external RCA, incident review or postmortem into one validated knowledge entry. It routes each source type (paste/incident/task/wiki page, code review diff, document, forum post, web article, pasted text) to a generic loader named in the command; ~/.claude/LOCAL.md maps each to the local tool.

Both commands are prompt files; the real specification lives in `dexter/references/`:
- `METHODOLOGY.md` — the seven non-negotiable properties and the phased loop. **This is the source of truth for how `/dexter:solve` behaves.** Edit behavior here, not just in the command file.
- `KNOWLEDGE-SCHEMA.md` — the required shape of a knowledge entry (the "no holes" quality bar).
- `HYPOTHESIS-TEMPLATE.md` — scaffold for a hypothesis.

### The two scripts and their invariants

`dexter/scripts/kg.py` — **append-only** per-investigation knowledge graph (`$DEXTER_HOME/cases/<id>/knowledge-graph/graph.jsonl`). Every mutation is a new JSONL line; nothing is ever rewritten, so full history (including status changes) is preserved. Current state is *folded* from the log on read (`render`/`show`). Node types: `question|fact|hypothesis|experiment|observation`. Edge rels: `supports|refutes|motivates|depends_on|tests|answers`. A hypothesis only becomes `confirmed`/`refuted` via a `verdict`, which warns if given fewer than 2 independent job IDs (the multi-test rule).

`dexter/scripts/kb.py` — the cross-investigation knowledge base. `validate` enforces the no-holes schema (all frontmatter keys incl. all five `environment` sub-keys, `>=1` generic source ref, all seven body sections non-stub, `>=1` numeric data point, no placeholder tokens) and the privacy rule (no private identifier, URL, absolute home/data path, email or denylist term); it exits non-zero and lists gaps. `scan` runs the privacy check over every file in the knowledge base, `digest` checks `LESSONS-DIGEST.md` covers every lesson, `lesson <id>` prints one lesson, and `search` ranks entries, lessons and the private refs files. Note: `kb.py` deliberately **has no third-party dependencies** — it ships its own minimal frontmatter parser rather than importing PyYAML. Keep it that way.

Shell helpers: `new_investigation.sh <slug>` (generate `<slug>-<YYYYMMDD-HHMMSS>` ID + scaffold the case dir), `log.sh` (append to `JOURNAL.md`), `record_job.sh` (record a test job + best-effort snapshot the working-copy diff via `sl` or `git`; the snapshot is refused when the denylist is missing or the diff holds a private term).

### Methodology invariants that must survive edits

These are the point of the plugin — preserve them when changing prompts or scripts:
- **Goal-driven completion** — done means the goal's definition-of-done is verified, not "root cause found." Finding the cause is a milestone; a confirmed RCA with no verified fix is still `IN PROGRESS`.
- **Multi-test verdicts** — no `confirmed`/`refuted` from a single run; `>=2` independent tests must agree and competitors be excluded.
- **Static baseline** — frozen before experimenting; later contradictions are graph nodes, not edits.
- **Fastest viable feedback loop** — pick the cheapest tier (local before remote) that still exercises the mechanism, with a representativeness check.
- **No holes** in banked knowledge — `kb.py validate` must pass before `kb.py index`.
- **Generic knowledge base** — entries and lessons describe systems by role; real ids, URLs, internal names and people go in `$DEXTER_HOME/refs/`. `kb.py validate`, `kb.py digest` and `kb.py scan` must all pass.

## Working with scripts

dexter has a stdlib test suite: `python3 dexter/scripts/test_kb.py`. To exercise the scripts by hand, point them at temp runtime dirs, e.g.:

```bash
# kb.py: validate a template against the schema
python3 dexter/scripts/kb.py template my-slug > /tmp/e.md   # NB: template has placeholders, so this is INVALID by design
INVESTIGATIONS_DIR=/tmp/inv DEXTER_HOME=/tmp/dx python3 dexter/scripts/kb.py validate /tmp/e.md

# kg.py: scaffold a case and drive the graph
INV_BASE=/tmp/cases dexter/scripts/new_investigation.sh demo
INV=/tmp/cases/$(cat /tmp/cases/.current)
python3 dexter/scripts/kg.py "$INV" node --id H1 --type hypothesis --text "..."
python3 dexter/scripts/kg.py "$INV" show
```

Adding a plugin: create `<name>/.claude-plugin/plugin.json`, a `commands/` dir (auto-discovered; no `commands` key needed in the manifest), and symlink it into `~/.claude/plugins/local/<name>`.
