# Authoring a Hermes playbook — quick-start

This is the workflow the `/hermes:new-playbook` command follows. The **authoritative
contract** (exact method signatures and field shapes) is the Hermes repo's
`docs/AUTHORING-PLAYBOOKS.md` — read it; this file is the recipe, that file is the spec.

## What a playbook is

A playbook is the "job" axis of Hermes' four extension points (playbook = the job, site =
the environment/tools, agent = the worker runtime, engine = the generic core). It defines a
run's phases, turns work into tickets, tells the agent what command to run, verifies each
result, and reduces results across the crew. Nothing auto-ships: `verify` and `reduce` are
the safety gates.

## The Playbook protocol (structural — a plain class, no inheritance)

Implement these (see `engine/playbook.py` for exact signatures, `engine/models.py` for the
field shapes):

- `name: str`, `phases: list[str]`
- `seed(run, site) -> list[Ticket]` — one ticket per unit of work. Goals usually come from
  `run.config["goals"]` (the `--goals FILE`) or `site.issue_source(IssueQuery(...))`.
- `payload_schema(phase) -> dict` / `result_schema(phase) -> dict` — the contract shapes
  (validated by `engine/contracts.py`).
- `driver(phase) -> Driver(command, args, loop)` — the command each ticket runs. For a Claude
  worker this renders as `"/goal <goal> /<your-command>"` (goal delivered via `/goal`).
- `verify(run, ticket, result, site) -> bool` — the ok→`reducing` gate; return `False` to
  route a ticket to `needs_human`. Do not trust the worker's self-report; fail safe.
- `reduce(run, phase, findings, site) -> list[Reduction]` — aggregate the phase's findings;
  put any `needs_human_ticket_ids` inside `reduction.json`. Must never raise.
- `next_phase(run) -> str | None`, `is_done(run) -> bool` — advancement.

Register on import: `from engine import playbook; playbook.register("<name>", MyPlaybook())`,
and re-export in `playbooks/<name>/__init__.py` (mirror `agents/claude/__init__.py`).

## Workflow

1. Read `docs/AUTHORING-PLAYBOOKS.md` + `engine/playbook.py` + `engine/models.py`. Clarify
   ambiguities (phases, work source, driver command, verify gate) before scaffolding.
2. **TDD**: write failing unit tests first (mirror `tests/unit/test_dexter_playbook.py`), then
   implement `playbooks/<name>/playbook.py`. Keep code self-contained — no doc/spec references
   in comments.
3. **Register via discovery, not an engine edit**: set `HERMES_PLAYBOOK_MODULES=playbooks.<name>`
   (dynamic discovery imports it for its `register()` side-effect). Never edit
   `engine/cli.py`'s loader for a custom playbook.
4. **Verify**:
   ```
   hermes doctor
   HERMES_PLAYBOOK_MODULES=playbooks.<name> \
     hermes run <name> --site local --agent claude --goals goals.txt --dry-run
   ```
   The dry run should seed one ticket per goal. Drop `--dry-run` to execute; scale to a crew
   per `docs/RUNBOOK.md`.
5. Run the suite (`./.venv/bin/python -m pytest -m "not docker" -q`) green, commit on the
   repo's branch convention.

## References
- `docs/AUTHORING-PLAYBOOKS.md` — authoritative authoring guide (contract + skeleton).
- `playbooks/dexter/playbook.py` — full worked example (cross-host reduce).
- `testkit/example_playbook.py` — minimal example.
- `docs/RUNBOOK.md` — operations (deploy, run topology, shutdown, backup).
