---
description: Scaffold and kick off a custom Hermes playbook from a plain-English description of the job. Designs the phases/seed/driver/verify/reduce, implements the Playbook protocol with TDD, verifies with a dry run, then runs it. Defaults to an in-repo playbook loaded by discovery; pass --local to scaffold a private, host-only playbook under $HERMES_HOME/local that is never committed. Not complete until the playbook loads, its tests pass, and a dry run seeds tickets.
argument-hint: <playbook name> — <what the job does, its phases, and where work comes from (goals/issues)> [--local]
---

You are authoring a new **Hermes playbook** (the "job" extension axis) from the description below, then kicking it off.

**Playbook request:** $ARGUMENTS

**Mode:** if the request includes `--local` (or asks for a private / host-only / internal playbook that must not be committed), use **LOCAL mode**; otherwise **REPO mode**. LOCAL mode scaffolds the playbook under `$HERMES_HOME/local/` (default `~/.hermes/local/`), which Hermes auto-discovers with no env vars and which lives OUTSIDE the shared repo (for private/internal-infra code). REPO mode scaffolds an in-repo playbook under `playbooks/<name>/`.

Work in the Hermes repo (default `~/workspace/hermes`; confirm the path if the cwd isn't it). Follow the workflow in `${CLAUDE_PLUGIN_ROOT}/references/AUTHOR-PLAYBOOK.md` — read it now. The **canonical contract** is the repo's `docs/AUTHORING-PLAYBOOKS.md` (read it; source of truth for signatures + field shapes, incl. its "Local / private playbooks" section). Use **superpowers:test-driven-development** (invoke it) for the implementation.

Do these in order; do not skip:

1. **Confirm the shape.** Read `docs/AUTHORING-PLAYBOOKS.md` + `engine/playbook.py` (the real `Playbook` protocol) + `engine/models.py` (Ticket/Driver/Finding/Reduction shapes). If the description is ambiguous on phases, where work comes from (a goals file vs `site.issue_source`), the driver command, or what `verify` gates on, ask before scaffolding. Decide LOCAL vs REPO mode.
2. **Design the playbook.** State briefly: `name`, `phases`, how `seed` turns work into tickets, the `driver` command each ticket runs (usually `/goal <goal> /<your-command>`), what `verify` checks (the ok→reducing gate; fail-safe to needs_human), and what `reduce` aggregates. Nothing auto-ships — `verify`/`reduce` are the safety gates.
3. **TDD the playbook.** Write failing tests first, watch them fail, then implement a plain class conforming to the protocol structurally (no inheritance) with `playbook.register("<name>", ...)` at import. Keep code self-contained: **no doc/spec references in code comments** (repo memory). Location by mode:
   - **REPO mode:** `playbooks/<name>/playbook.py` + `playbooks/<name>/__init__.py` re-export; tests in `tests/unit/`.
   - **LOCAL mode:** `$HERMES_HOME/local/<name>.py` (a single module that registers on import; or a package dir). Do NOT put it in the repo. Test it with a small standalone test you run against it (or a temp `HERMES_HOME`), since it lives outside the repo's `tests/`.
4. **Load it.**
   - **REPO mode:** loads via discovery — `HERMES_PLAYBOOK_MODULES=playbooks.<name>` (do NOT edit `engine/cli.py`). Verify: `HERMES_PLAYBOOK_MODULES=playbooks.<name> hermes run <name> --site local --agent claude --goals goals.txt --dry-run`.
   - **LOCAL mode:** ZERO-config — it auto-loads from `$HERMES_HOME/local/` with no env vars. Verify: `hermes doctor` (shows it registered), then `hermes run <name> --site local --agent claude --goals goals.txt --dry-run`.
   Either way the dry run should seed one ticket per goal.
5. **Run it (only if asked to kick off for real).** Drop `--dry-run` to execute on the local site, or point at a crew per `docs/RUNBOOK.md`. Report the run id and how to watch it (`hermes status`, or the control-plane UI).
6. **Finish.**
   - **REPO mode:** run the suite (`./.venv/bin/python -m pytest -m "not docker" -q`) green, then commit the playbook + tests on the repo's branch convention (do not push unless asked).
   - **LOCAL mode:** do NOT commit to the hermes repo — the module stays host-local under `$HERMES_HOME/local/`. Confirm the repo working tree is unchanged. If the code references internal/private infra, remind the user it is intentionally uncommitted.

Report at each step. Do not claim done until the playbook loads (via discovery or the local dir), its tests pass, and a dry run seeds the expected tickets. If the goal was only to scaffold (not run), stop after the dry run and say so.
