---
description: Scaffold and kick off a custom Hermes playbook from a plain-English description of the job. Designs the phases/seed/driver/verify/reduce, implements the Playbook protocol with TDD, registers it via dynamic discovery (HERMES_PLAYBOOK_MODULES — no engine edit), verifies with a dry run, then runs it across a crew. Not complete until the playbook loads, its tests pass, and a dry run seeds tickets.
argument-hint: <playbook name> — <what the job does, its phases, and where work comes from (goals/issues)>
---

You are authoring a new **Hermes playbook** (the "job" extension axis) from the description below, then kicking it off.

**Playbook request:** $ARGUMENTS

Work in the Hermes repo (default `~/workspace/hermes`; confirm the path if the cwd isn't it). Follow the authoring workflow in `${CLAUDE_PLUGIN_ROOT}/references/AUTHOR-PLAYBOOK.md` — read it now. The **canonical contract** is the repo's `docs/AUTHORING-PLAYBOOKS.md`; read that too and treat it as the source of truth for method signatures and field shapes (the bundled reference is a quick-start that points at it). Use **superpowers:test-driven-development** (invoke it) for the implementation.

Do these in order; do not skip:

1. **Confirm the repo + the shape.** Locate the Hermes repo and read `docs/AUTHORING-PLAYBOOKS.md` + `engine/playbook.py` (the real `Playbook` protocol) + `engine/models.py` (Ticket/Driver/Finding/Reduction shapes). If the description is ambiguous on phases, where work comes from (a goals file vs `site.issue_source`), the driver command, or what `verify` should gate on, ask before scaffolding.
2. **Design the playbook.** State, briefly: `name`, `phases`, how `seed` turns work into tickets, the `driver` command each ticket runs (usually `/goal <goal> /<your-command>`), what `verify` checks (the ok→reducing gate; fail-safe to needs_human), and what `reduce` aggregates. Nothing auto-ships — `verify`/`reduce` are the safety gates.
3. **TDD the playbook.** Write failing unit tests first (mirror `tests/unit/test_dexter_playbook.py`: seed shape, schema accept/reject via `contracts`, driver rendering, verify branches, reduce clustering), watch them fail, then implement `playbooks/<name>/playbook.py` (a plain class conforming to the protocol structurally — no inheritance) with `playbook.register("<name>", ...)` at import and a `playbooks/<name>/__init__.py` re-export. Keep the code self-contained: **no references to the docs/specs in code comments** (see the repo's memory on this).
4. **Register via discovery, not an engine edit.** The playbook loads through `HERMES_PLAYBOOK_MODULES=playbooks.<name>` (dynamic discovery) — do NOT edit `engine/cli.py`'s loader. Verify: `hermes doctor` shows it, then `HERMES_PLAYBOOK_MODULES=playbooks.<name> hermes run <name> --site local --agent claude --goals goals.txt --dry-run` seeds tickets.
5. **Run it (only if asked to kick off for real).** Drop `--dry-run` to execute on the local site, or point at a crew per `docs/RUNBOOK.md`. Report the run id and how to watch it (`hermes status`, or the control-plane UI).
6. **Verify + commit.** Run the repo's test suite (`./.venv/bin/python -m pytest -m "not docker" -q`) green + pristine, then commit the new playbook + tests on the repo's branch convention (commit as you go; do not push unless asked).

Report at each step. Do not claim done until the playbook loads via discovery, its tests pass, and a dry run seeds the expected tickets. If the goal was only to scaffold (not run), stop after the dry run and say so.
