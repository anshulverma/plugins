# Foreman engine core — implementation plan (sub-project 1)

Status: **draft**. Date: 2026-07-28. Spec: `foreman/docs/specs/engine-core.md`.

Vertical slices in dependency order. Each slice is independently testable, follows
**TDD** (write the failing test first, then the code), and ends GREEN before the
next begins. Every slice lists its deliverables, tests, and acceptance criteria.
Engine core is **stdlib-only at runtime**; `pytest` is a dev-only dependency.

Conventions: paths are under `foreman/`. "GREEN" = `scripts/run_tests.sh` passes.
Commit after each slice.

---

## Slice 0 — Scaffold

**Deliverables**
- `.claude-plugin/plugin.json` (name `foreman`, commands glob, skills), `commands/`
  stubs, `skills/foreman/SKILL.md` stub.
- `engine/__init__.py`, `engine/config.py` (`resolve_home()`, env vars:
  `FOREMAN_HOME`, `FOREMAN_HEARTBEAT_S=30`, `FOREMAN_SITE=local`, `FOREMAN_BIND`,
  networked-mount guard), `pyproject.toml` (dev-deps: pytest), `scripts/run_tests.sh`.
- `tests/` package skeleton.

**Tests** — `config` resolves default `~/.foreman`, honors `FOREMAN_HOME`, and
rejects a networked-mount path with a clear error.

**Acceptance** — `run_tests.sh` runs (0 tests failing); `config` tests green.

---

## Slice 1 — DB schema + migrations

**Deliverables** — `db/schema.sql` (§4 DDL), `db/migrate.py`
(`apply_migrations(path)` idempotent, `connect(path)` with PRAGMAs + 0600).

**Tests** — apply on empty file creates all tables + indexes; re-apply is a
no-op; `schema_migrations` records versions; file mode is 0600; WAL enabled.

**Acceptance** — migrations idempotent; a fresh `queue.db` opens with all tables.

---

## Slice 2 — Models + contracts

**Deliverables** — `models.py` (dataclasses from §8/§6: `Run, Ticket, Attempt,
Result, HealthReport, Check, Driver, GoalEnvelope, Reduction, Finding, IssueQuery,
Issue, Lease, CrewMember`); `contracts.py` (validator + `DISPATCH_ENVELOPE`,
`GOAL_ENVELOPE`, `RESULT_OUTER` schemas + `validate_envelope`, `validate_result`).

**Tests** — validator accepts valid docs; rejects wrong type, missing required,
unexpected key (`additionalProperties:false`), bad enum, nested `items`; envelope
+ result layering (outer + playbook sub-schema) accept/reject; `ContractError`
carries a JSON path.

**Acceptance** — spec §6 contract behavior fully covered; malformed docs raise
`ContractError`, never pass.

---

## Slice 3 — Events

**Deliverables** — `events.py` (`emit`, `since`, `tail`; the §7 `kind` set).

**Tests** — `emit` appends; `since(after_id)` returns ordered rows > id; `tail(n)`
returns last n; `data` round-trips JSON.

**Acceptance** — ordering is monotonic by `id`; feed is append-only.

---

## Slice 4 — Interfaces + LocalSite skeleton + testkit example

**Deliverables** — `playbook.py` (`Playbook` Protocol + registry `register`/
`load`), `site.py` (`Site` Protocol + registry, `HealthReport.ok`),
`sites/local/site.py` (`LocalSite`: `health`, `resource_classes`,
`guarantees_no_ship`, `provision` = git worktree; `run_worker` deferred to
Slice 7), `testkit/example_playbook.py` (`EchoPlaybook`),
`testkit/mock_agent.py` (scenario-table fake worker), `testkit/fixtures.py`
(temp `FOREMAN_HOME`, canned issue file).

**Tests** — registry resolves playbook/site by name; `HealthReport.ok` is True iff
all checks pass; `LocalSite.health` reports failing checks individually;
`EchoPlaybook.seed` yields tickets from the canned issues; mock agent writes the
scenario's `result.json` for a given envelope.

**Acceptance** — an `EchoPlaybook` + `LocalSite` pair loads and seeds; mock agent
produces each Result outcome deterministically.

---

## Slice 5 — Queue + state machine

**Deliverables** — `queue.py` (`seed_tickets`, `claim_ticket` with
`BEGIN IMMEDIATE`, `record_result` applying every §5 transition + `attempts` row +
`findings` insert + events, `requeue`/`requeue_transport`).

**Tests** (table-driven over §5) — each transition: ok→done/reducing; ok+verify
False→needs_human; driver_failed→failed; infra_failed retry then cap→failed with
backoff; transport→queued no penalty; concurrent `claim_ticket` from N threads
yields N distinct tickets (atomicity); `tried_hosts` accumulates.

**Acceptance** — every §5 edge exercised; no double-claim under concurrency.

---

## Slice 6 — Leases

**Deliverables** — `leases.py` (`acquire` under a per-class semaphore from
`site.resource_classes` capacity, `renew`, `reclaim_expired` requeuing tickets).

**Tests** — acquire succeeds under capacity, returns None at capacity (caller
parks); `renew` extends `expires_at`; `reclaim_expired` frees + requeues; TTL ≫
heartbeat invariant asserted.

**Acceptance** — semaphore never over-issues; expired leases self-heal.

---

## Slice 7 — Transport + drivers

**Deliverables** — `drivers.py` (`build_argv` → `claude -p "/goal …"
--permission-mode bypassPermissions` + optional methodology command;
`parse_result`), `transport.py` (`local_transport`, `ssh_transport`,
`serve_once_for_host`), wire `LocalSite.run_worker` to `local_transport` (mock
agent when `FOREMAN_MOCK_AGENT=1`).

**Tests** — `build_argv` sets goal + permission mode + timeout wrapper, includes
methodology command when present, omits when null; `parse_result` maps outputs to
Result; `serve_once_for_host` on `LocalSite`+mock agent claims→leases→runs→records
one ticket; envelope error ⇒ penalty requeue; simulated transport error ⇒
no-penalty requeue + host down.

**Acceptance** — one ticket flows claim→run→record end-to-end with the mock agent.

---

## Slice 8 — Crew + health + heartbeat

**Deliverables** — `crew.py` (`add` = provision+health-gate, `list`, `drain`,
`remove`, `heartbeat_sweep` = re-probe/update/down-requeue/renew/reclaim/re-admit).

**Tests** — `add` admits a healthy host, rejects an unhealthy one listing failing
checks; `heartbeat_sweep` marks a now-unreachable host `down` and requeues its
in-flight ticket without penalty; a recovered host is re-admitted; sweep renews
live leases and reclaims expired ones.

**Acceptance** — spec §7 health-gating + heartbeat behavior covered.

---

## Slice 9 — Dispatch loops + end-to-end integration

**Deliverables** — `dispatch.py` (`serve_loop`, `master_loop` driving heartbeat +
phase advancement via `reduce`/`next_phase`/`seed` + run termination on
`is_done`).

**Tests (integration)** — full pipeline on `LocalSite`+mock agent+EchoPlaybook:
seed→dispatch→run→reduce→advance→done; assert terminal ticket states, run→done,
the ordered event stream, reduction creation; the no-ship guard **blocks** a
`git push` in a worker context; a malformed envelope/result aborts the ticket as
`driver_failed/contract_fail` (dry-run NO-GO analog).

**Acceptance** — the engine drives a real multi-phase run to completion locally
with zero Meta/SSH/real-claude dependency.

---

## Slice 10 — CLI

**Deliverables** — `cli.py` + `commands/` (`run`, `serve`, `crew`, `status`,
`show`, `--dry-run`), console entrypoint `foreman`.

**Tests** — `run --dry-run` seeds+reports, no dispatch; `run` (local) reaches a
terminal run; `crew add` prints health + admits/refuses; `status` renders
run/ticket/crew/lease/attention from `queue.db`; `show` prints envelope/result/
attempts.

**Acceptance** — spec §10 commands work against a temp `FOREMAN_HOME`.

---

## Slice 11 — Hardening & invariant tests

**Deliverables** — invariant tests + docs polish (`README`, SKILL fill-in).

**Tests** — engine core imports **no third-party package** at runtime (import
scan); `queue.db` refuses a networked mount; 0600 enforced; attention events fire
on `parked_ratio>0.5` / `all_crew_down` / `no_progress>1800s`; `verify=False`
routes to `needs_human` end-to-end.

**Acceptance** — all spec §13 acceptance criteria pass; `run_tests.sh` ALL GREEN.

---

## Dependency graph

```
0 ─▶ 1 ─▶ 2 ─▶ 3
          └─▶ 4 ─▶ 5 ─▶ 6 ─▶ 7 ─▶ 8 ─▶ 9 ─▶ 10 ─▶ 11
```

Slices 2 and 3 depend only on 1; 4 depends on 2; the main chain 4→11 is linear.
2 and 3 may be built in parallel after 1.

## Test tooling

`pytest` (dev-only), tests under `tests/unit` and `tests/integration`.
`scripts/run_tests.sh` runs both and reports ALL GREEN / a failure list. No
network, no SSH, no real `claude`, no Meta in any test — the `local` site + mock
agent provide full coverage.
