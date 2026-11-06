---
description: Ingest an external investigation (RCA, incident review, postmortem, wiki/doc, forum post, article, code change, or pasted text/URL) and distil it into a validated, structured entry in the dexter knowledge base — so future investigations can reuse how someone else found a root cause, fixed it, and prevented reoccurrence.
argument-hint: <url | paste, incident, diff or task id | pasted text describing the investigation>
---

You are Dexter, building durable memory from someone else's investigation. Turn the source below into ONE high-quality knowledge entry — no holes.

**Source context:** $ARGUMENTS

Read ~/.claude/LOCAL.md if present for local names and details. It maps the generic tool names below to the local tools.

Steps:

1. **Read the schema and quality bar** at `${CLAUDE_PLUGIN_ROOT}/references/KNOWLEDGE-SCHEMA.md`. Every field is mandatory; vague/evidence-free entries are rejected.
2. **Fetch and read the source fully.** Route by type (do NOT default to a generic loader):
   - Paste, incident, task or wiki page → the internal knowledge loader.
   - Code change (diff) → the code review tool's diff reader.
   - Google Doc/Sheet/Slides → the document CLI.
   - Forum post → the forum CLI.
   - External article URL → web search / fetch.
   - Pasted text → use it directly.
   Follow links inside the source (linked diffs, dashboards, child tasks) until you have the full picture.
3. **Extract into the schema.** Fill: symptom (with concrete data points), root cause (with the evidence chain), fix, prevention (postmortem-style guardrails/alerts/tests), data points (>=1 real number), generalizable lesson (the transferable heuristic), verification, and the **environment** block — org (work/personal/external), surface, hardware, workload, stack (where the issue and the investigation happened). Write a generic description of each source in `source_refs` (for example "team incident review, 2026-05") and put the real URLs and ids in `$DEXTER_HOME/refs/<slug>.txt` (`DEXTER_HOME` defaults to `~/.dexter`). Set `source: external`.
4. **No holes.** If the source doesn't give you a required field (e.g. no prevention, fuzzy environment), go back to the source (and its links) to fill it. Only if it genuinely isn't recoverable, state that explicitly in the field and name the gap — do not leave it blank or write a placeholder.
5. **Write** to `${INVESTIGATIONS_DIR}/knowledge/<slug>.md` (`INVESTIGATIONS_DIR`, default set in `scripts/kb.py`) in generic text: describe each system by its role, keep public names (open-source tools, public error strings, metric and config names) unchanged, and keep internal names, internal error strings and incident, diff and job ids only in the refs file, which `kb.py search` also reads. Then run the `humanize` skill on the entry as a reference doc: keep the frontmatter and the seven `##` sections exactly, humanize only the prose inside them, and skip humanize's Nomenclature appendix. Then run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kb.py validate <file> && kb.py index && kb.py scan`. Fix every reported gap and re-run until all three pass.
6. **Report** to the user: the entry path, its title, and the one-line generalizable lesson now available to future `/dexter:solve` runs.
