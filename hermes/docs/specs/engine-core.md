# Hermes engine core — spec (sub-project 1)

Status: **draft**. Date: 2026-07-28. Parent: `hermes/docs/DESIGN.md`.

This spec covers **sub-project 1** from the umbrella design's §13: the generic
engine, its `local` reference site, the `testkit` mock agent, and the full
unit + integration test suite. It deliberately **excludes** the FastAPI server
and React SPA (sub-project 3), the `mechanic`/`rigger` playbooks (sub-projects
2/4), and the `meta` site adapter (sub-project 2) — but it defines every
interface those depend on.

Terminology, architecture, data model, contracts, state machine, and driver
model are defined in `DESIGN.md`; this spec makes them implementation-ready.

---

## 1. Scope

**In scope**
- `engine/` python package (stdlib-only): db + migrations, queue, dispatch loop,
  transport, crew + health, leases, contracts, events, drivers, the `Playbook`
  and `Site` protocols + loaders, CLI.
- `sites/local/` — the reference site (localhost + git + shell) that runs the
  whole system on one box with no Meta/SSH.
- `testkit/` — a mock agent runner (a fake worker that reads an envelope and
  writes a deterministic result) + an example playbook + fixtures, used by tests.
- `tests/{unit,integration}` — full coverage; runnable via `run_tests.sh` with
  no third-party runtime deps for the engine (pytest is a dev-only dep).

**Out of scope (later sub-projects, but interfaces are fixed here)**
- HTTP server / websocket / SPA. The engine writes an append-only `events` table
  and exposes read helpers; the server (sub-project 3) will read them.
- Real playbooks and the `meta` site.

**Non-goals**
- No auto-landing, ever (enforced by construction; see §11).
- Engine core imports no third-party package at runtime (dev/test may use pytest).

---

## 2. Module layout & responsibilities

```
hermes/engine/
  __init__.py
  config.py        # HERMES_HOME resolution, env vars, paths, defaults
  db/
    schema.sql     # DDL (§4)
    migrate.py     # idempotent additive migration runner + connect()
  models.py        # dataclasses: Ticket, Result, HealthReport, Check, Driver,
                   #   GoalEnvelope, Reduction, IssueQuery, Issue, Lease, CrewMember
  contracts.py     # dependency-free JSON-schema-subset validator + envelope layering
  events.py        # append-only event log: emit(), tail(), since()
  queue.py         # seed_tickets, claim_ticket, record_result, requeue*, state machine
  leases.py        # acquire, renew, reclaim_expired
  crew.py          # register, add (provision+health-gate), drain, remove, heartbeat sweep
  drivers.py       # Driver -> headless `claude -p` argv; result parsing
  transport.py     # local_transport, ssh_transport, serve_once_for_host
  dispatch.py      # serve loop (per-host worker), master loop, reduce/advance driver
  playbook.py      # Playbook Protocol + registry/loader
  site.py          # Site Protocol + registry/loader
  cli.py           # `hermes` entrypoint: run, status, crew, serve, show

hermes/sites/local/site.py     # LocalSite(Site)
hermes/testkit/
  mock_agent.py    # fake worker: envelope in -> deterministic result out
  example_playbook.py            # EchoPlaybook(Playbook): 2 phases, trivial reduce
  fixtures.py      # temp HERMES_HOME, seeded runs, canned issues

hermes/tests/{unit,integration}/...
hermes/scripts/run_tests.sh
```

Each module is independently unit-testable; nothing outside `db/` and `config.py`
touches the filesystem for state, and nothing outside `transport.py`/`crew.py`
spawns subprocesses.

---

## 3. Runtime data layout (`HERMES_HOME`)

`config.resolve_home()` returns `$HERMES_HOME` or `~/.hermes`. Layout:

```
$HERMES_HOME/
  queue.db                 # SQLite, WAL, mode 0600 (§4)
  api_token                # bearer token (created by `serve`; sub-project 3 uses it)
  logs/                    # serve loop logs
  tickets/<ticket_id>/
    envelope.json          # dispatched GoalEnvelope (§6)
    result.json            # worker result (§6)
    evidence.*             # optional durable evidence pulled back from a worker
```

`queue.db` is **never** placed on a networked/again-synced filesystem (ported
invariant); `config` refuses a `HERMES_HOME` under a known-networked mount and
errors with a clear message.

---

## 4. Database schema (DDL)

SQLite, WAL, `synchronous=NORMAL`, `busy_timeout=5000`, `foreign_keys=ON`, file
mode `0600`. Additive-only migrations tracked in `schema_migrations`. Two writers
(the serve loop + the CLI) serialized by WAL + `BEGIN IMMEDIATE`.

```sql
CREATE TABLE runs (
  id          TEXT PRIMARY KEY,          -- <playbook>-<YYYYMMDD-HHMMSS>
  playbook    TEXT NOT NULL,
  site        TEXT NOT NULL,
  base_ref    TEXT NOT NULL,
  config_json TEXT NOT NULL DEFAULT '{}',
  state       TEXT NOT NULL              -- running|paused|stopped|done|failed
              CHECK(state IN ('running','paused','stopped','done','failed')),
  phase       TEXT,
  created_at  REAL NOT NULL, updated_at REAL NOT NULL
);

CREATE TABLE tickets (
  id           TEXT PRIMARY KEY,         -- <run_id>/t-<n>
  run_id       TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  phase        TEXT NOT NULL,
  state        TEXT NOT NULL             -- see §5 state machine
              CHECK(state IN ('queued','dispatched','running','reducing',
                              'done','parked','failed','needs_human')),
  resource_req TEXT NOT NULL DEFAULT 'cpu',
  priority     REAL NOT NULL DEFAULT 0,
  attempts     INTEGER NOT NULL DEFAULT 0,      -- infra-failure retries only (max 3)
  available_at REAL NOT NULL DEFAULT 0,
  lease_id     TEXT,
  worker_host  TEXT,
  reduction_id INTEGER REFERENCES reductions(id), -- reduction that routed this
                                                --   ticket to needs_human (§5, §9);
                                                --   INTEGER to match reductions.id
                                                --   (FK + §9 lookup require same type)
  tried_hosts  TEXT NOT NULL DEFAULT '[]',      -- JSON array
  payload_json TEXT NOT NULL DEFAULT '{}',      -- playbook payload for this phase
  created_at   REAL NOT NULL, updated_at REAL NOT NULL
);

CREATE TABLE attempts (                          -- append-only audit
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id     TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  phase         TEXT NOT NULL, host TEXT NOT NULL, attempt INTEGER NOT NULL,
  started_at    REAL, ended_at REAL,
  outcome       TEXT,   -- ok|driver_failed|infra_failed  (see §6 Result)
  termination_reason TEXT, -- goal_met|contract_fail|driver_error|timeout|transport_error
  result_ref    TEXT, error_summary TEXT
);

CREATE TABLE findings (                           -- generic per-ticket result doc
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id    TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  kind      TEXT NOT NULL, json TEXT NOT NULL, created_at REAL NOT NULL
);

CREATE TABLE reductions (                         -- master-side aggregate output
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL, json TEXT NOT NULL,
  review_state TEXT NOT NULL DEFAULT 'pending'
              CHECK(review_state IN ('pending','accepted','rejected','superseded')),
  created_at   REAL NOT NULL, updated_at REAL NOT NULL
);

CREATE TABLE crew (
  id             TEXT PRIMARY KEY,        -- host id
  site           TEXT NOT NULL, capabilities TEXT NOT NULL DEFAULT '[]',
  resources_json TEXT NOT NULL DEFAULT '{}',
  state          TEXT NOT NULL            -- idle|busy|down|draining
                CHECK(state IN ('idle','busy','down','draining')),
  health_json    TEXT, current_ticket TEXT, last_heartbeat REAL,
  registered_at  REAL NOT NULL
);

CREATE TABLE leases (
  id            TEXT PRIMARY KEY,
  run_id        TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  resource_class TEXT NOT NULL,
  ticket_id     TEXT, host TEXT,
  acquired_at   REAL NOT NULL, ttl_s INTEGER NOT NULL DEFAULT 1800,
  expires_at    REAL NOT NULL
);

CREATE TABLE events (                             -- append-only feed (§7)
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        REAL NOT NULL, kind TEXT NOT NULL,
  run_id    TEXT, ticket_id TEXT, host TEXT,
  message   TEXT, data_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL, description TEXT);

CREATE INDEX idx_tickets_dispatch ON tickets(run_id, state, available_at, priority);
CREATE INDEX idx_tickets_resource ON tickets(state, resource_req);
CREATE INDEX idx_attempts_ticket ON attempts(ticket_id);
CREATE INDEX idx_events_stream ON events(id);
CREATE INDEX idx_findings_run ON findings(run_id);
```

---

## 5. Ticket state machine

States and their entry/exit (authoritative; mirrors `DESIGN.md` §5):

```
queued ──claim──▶ dispatched ──worker starts──▶ running
running ──outcome=ok & verify=True──▶ reducing         (uniform reduce gate, every phase)
running ──outcome=ok & verify=False─▶ needs_human      (re-verify override)
running ──outcome=driver_failed─────▶ failed           (terminal, no retry)
running ──outcome=infra_failed & attempts<3─▶ queued   (attempts+1, available_at=now+backoff)
running ──outcome=infra_failed & attempts=3─▶ failed
running ──host lost (transport error)──▶ queued        (NO attempt penalty; host->down)
dispatched ──no lease for its class──▶ parked          (claimed but unleased; NO attempt penalty; claim reverted)
parked  ──its class regains capacity──▶ queued         (fresh claim; NO attempt penalty)
reducing ──playbook.reduce keeps the result──▶ done    (per-ticket; run advances phase)
reducing ──reduce flags it (needs_human_ticket_ids)──▶ needs_human  (reduction_id set)
needs_human ──reduction accepted──▶ done
needs_human ──reduction rejected──▶ failed
needs_human ──operator requeue──▶ queued  (re-verify/guard-routed tickets)
```

- `attempts` counts **infra** retries only, capped at 3; a 4th infra failure ⇒
  `failed`. Backoff: `available_at = now + min(300, 30 * 2**attempts)` seconds.
- A driver-reported failure (`outcome=driver_failed`, e.g. `contract_fail`,
  `driver_error`, `timeout`) is terminal on first occurrence.
- `done`, `failed` are terminal. `failed` and `needs_human` each emit an
  attention-banner event; the banner is an attribute of the state and clears when
  the ticket leaves it.

**Run state machine** (`runs.state`, resolving the umbrella §14 deferral):

```
running ──playbook.is_done(run)==True──▶ done          (all work complete)
running ──no actionable tickets left & not is_done──▶ failed
                                          (no ticket is queued/dispatched/running/
                                           reducing/parked/needs_human — i.e. every
                                           ticket is done or failed — and next_phase==None)
running ──control: pause──▶ paused ──control: resume──▶ running
running|paused ──control: stop──▶ stopped              (terminal)
```

- `running` is the initial state (set by `hermes run`). `done`, `failed`,
  `stopped` are terminal.
- `pause`/`resume`/`stop` are **control actions**, each applied by the engine
  callable `queue.set_run_state(conn, run_id, target, now)` (the single function
  that mutates `runs.state`; §9). All three have a CLI surface in this sub-project
  via `hermes run {pause|resume|stop} <run_id>` (`hermes run <playbook>` starts a
  run); the server sub-project 3 additionally exposes them over HTTP. The engine
  must honor all three states regardless of caller. `set_run_state` is the single
  function that transitions `runs.state` (initial `running` set at creation) and
  enforces every legal edge above — the control edges
  (running↔paused, running|paused→stopped) plus the automatic terminal edges
  `master_loop` drives (running→done on `is_done`, running→failed when stuck) — and
  rejects any other transition (e.g. resuming a `stopped`/`done`/`failed` run) with
  an error; pause/resume/stop raise on an already-terminal run.
- **Dispatch halting & pause freeze:** `claim_ticket` only considers tickets whose
  owning run is `running`; tickets of a `paused`/`stopped`/`done`/`failed` run are
  never claimed. Beyond claiming, a non-`running` run makes **no progression at
  all**: while a run is `paused`, `master_loop` performs no claim, no `reduce`, no
  phase advancement, and no `seed`, and drives no automatic terminal transition
  (running→`done`/`failed`). Only reclaim/heartbeat housekeeping (health re-probe,
  down-requeue, lease renew/reclaim) continues, so a ticket already dispatched
  before the pause can still finish and be recorded (landing in `reducing`, where it
  waits until `resume`); a resumed run picks progression back up. A `stopped` run's
  non-terminal tickets are left as-is (no auto-transition).

---

## 6. Contracts & envelopes

`contracts.validate(instance, schema)` is the ported dependency-free validator
(supports `type` incl. nullable unions, `required`, `properties`,
`additionalProperties:false`, `enum`, `items`). A mismatch raises
`ContractError(path, detail)`.

**Dispatch envelope** (engine → worker), `additionalProperties:false`:

```json
{ "ticket_id","run_id","phase","resource_req","base_ref","payload",
  "payload_sha256","timeout_s","site_context","goal_envelope" }
```

`payload` is the playbook's per-phase payload doc (validated against
`payload_schema(phase)`); `payload_sha256` is the SHA-256 hex digest of that
payload's canonical (sorted-key, no-whitespace) JSON encoding, carried for
integrity — a mismatch on the worker side is a `contract_fail`.

**GoalEnvelope** (value of `goal_envelope`): `{ "goal": str,
"driver": {"command": str|null, "args": obj, "loop": str|null}, "done_contract": obj,
"guardrails": {"no_ship": bool} }`. `done_contract` is the required result schema
(the playbook's `result_schema(phase)`, per DESIGN §6); `driver.loop` is the
optional `/loop` interval for polling-style drivers (null otherwise);
`guardrails.no_ship` defaults `true`.

**Result** (worker → engine): a JSON doc validated against the phase's
`result_schema`, plus engine-owned outer fields:
`{ "outcome": "ok"|"driver_failed"|"infra_failed",
"termination_reason": one of goal_met|contract_fail|driver_error|timeout|transport_error,
"result_ref": str|null, "evidence_ref": str|null,
"started_at": num, "ended_at": num, "error_summary": str|null,
"payload": <playbook doc> }`. `started_at`/`ended_at` are epoch seconds set by
the site's `run_worker`; `started_at`, `ended_at`, `outcome`,
`termination_reason`, `result_ref`, and `error_summary` are what `record_result`
persists into the `attempts` audit row (§4). The `payload` sub-doc is validated
against `result_schema(phase)` only when `outcome=="ok"`.

`timeout_s` is the **single** wall-clock budget (default 3600; per-deployment
cap). There is no turn-based limit. On dispatch the engine validates the envelope
and the playbook payload sub-schema; on result it validates the Result outer
schema and (if ok) the payload sub-schema. Any failure ⇒ `driver_failed` /
`contract_fail`.

---

## 7. Events

`events.emit(conn, kind, *, run_id=None, ticket_id=None, host=None, message=None,
data=None)` appends one row. `events.since(conn, after_id, limit=200)` returns
ordered rows for polling; `events.tail(conn, n)` for the CLI. Event `kind`s the
engine emits: `run_started run_paused run_resumed run_stopped run_done run_failed ticket_claimed
ticket_started result_recorded ticket_requeued ticket_parked ticket_failed
needs_human phase_advanced reduction_created reduction_accepted reduction_rejected
crew_added crew_health crew_down crew_drained lease_acquired lease_reclaimed
attention`. Attention conditions (emit `attention` with a `reason`):
`parked_ratio>0.5`, `all_crew_down`, `no_progress>1800s`, `needs_human`, `failed`.

---

## 8. Interfaces

### Playbook (`playbook.py`)

```python
class Playbook(Protocol):
    name: str
    phases: list[str]
    def seed(self, run: Run, site: Site) -> list[Ticket]: ...
    def payload_schema(self, phase: str) -> dict: ...
    def result_schema(self, phase: str) -> dict: ...
    def driver(self, phase: str) -> Driver: ...
    def reduce(self, run: Run, phase: str, findings: list[Finding], site: Site) -> list[Reduction]: ...
    def verify(self, run: Run, ticket: Ticket, result: Result, site: Site) -> bool: ...
    def next_phase(self, run: Run) -> str | None: ...
    def is_done(self, run: Run) -> bool: ...
```

`verify` default returns `True` (nothing to re-check). Playbooks are registered
by entry name; `playbook.load(name)` resolves from a registry populated by
imported playbook plugins (and the `testkit` example).

The `Run` snapshot passed to the playbook methods carries `id, playbook, site,
base_ref, config` (parsed `config_json`), `phase` (the phase currently being
seeded/advanced), and `reductions` (the prior phase's `Reduction` list, empty for
phase 0). It is a read-only view; playbooks mutate engine state only by returning
`Ticket`/`Reduction` values.

### Site (`site.py`)

```python
class Site(Protocol):
    name: str
    def discover_hosts(self) -> list[str]: ...
    def provision(self, host: str, base_ref: str) -> None: ...
    def health(self, host: str) -> HealthReport: ...
    def run_worker(self, host: str, envelope: dict) -> Result: ...
    def resource_classes(self) -> list[str]: ...
    def guarantees_no_ship(self) -> bool: ...
    def submit_for_review(self, host: str, change: dict) -> str: ...   # review URL; never lands
    def issue_source(self, query: IssueQuery) -> list[Issue]: ...
```

`HealthReport{reachable, agent_ok, auth_ok, workspace_ready, guard_installed,
resources: dict, latency_ms: int, checks: list[Check]}`; `ok` is True iff every
`Check` passed. `Check{name, ok, detail}`. `Result` as in §6. Per DESIGN §3:
`IssueQuery{kind: str, filters: dict={}, limit: int=100}`;
`Issue{id: str, kind: str, title: str, ref: str, data: dict}` (`kind` echoes the
query's `kind`, `ref` is a URL/path back to the source of record).

### LocalSite (`sites/local/site.py`)

Runs everything on `localhost`: `provision` = ensure a git worktree at `base_ref`;
`health` = check python/`claude` present, worktree clean, guard shims installed,
`resources={"cpu": os.cpu_count()}`; `run_worker` = `transport.local_transport`
invoking the driver via `claude -p` (or the `testkit` mock agent in tests);
`resource_classes=["cpu"]`; `guarantees_no_ship=True` (installs PATH guard shims
that block `git push`/land-like commands, ported `land_guard`);
`submit_for_review` = create a local branch + return a `file://` "review" ref;
`issue_source` = read a JSON file named by the query filters (test/demo source).

---

## 9. Queue, dispatch, leases, crew, drivers

- **queue.py** — `seed_tickets(conn, run, playbook, site)` inserts tickets from
  `playbook.seed`; `claim_ticket(conn, host, resource_reqs, now)` atomically
  (`BEGIN IMMEDIATE`) selects the highest-priority `queued` ticket whose owning
  run is `running`, whose `resource_req` the host serves, and whose
  `available_at<=now`, sets `dispatched` +
  `worker_host` + appends `tried_hosts`; `record_result(conn, ticket, host,
  result, playbook, site, now)` applies the §5 transitions — for an
  `outcome==ok` result it evaluates `playbook.verify(run, ticket, result, site)`
  (the `run` snapshot loaded from the ticket's `run_id`) and routes
  `running → reducing` on True or `running → needs_human` on False (the §3 master
  re-verify override) — appends an `attempts` row, stores the
  playbook payload into `findings`, and emits events; `requeue` (penalty, infra
  retry) / `requeue_transport` (no-penalty, transport/host-lost) implement the two
  `running→queued` paths, and **each releases the ticket's held lease** as it leaves
  `running` (§leases below), so a scarce class frees immediately instead of after a
  full backoff/TTL.
  `park_ticket(conn, ticket, now)` handles the no-lease overflow: it reverts a
  just-claimed ticket `dispatched → parked` (clears `worker_host`, drops the host it
  just appended to `tried_hosts` since nothing executed, no attempt penalty) and
  emits `ticket_parked`; `unpark_ready(conn, resource_class, now)` returns `parked`
  tickets of a class to `queued` (fresh claim, no penalty, emits `ticket_requeued`)
  whenever that class has free capacity (per leases §above).
  `set_run_state(conn, run_id, target, now)` is the **only** function that
  *transitions* `runs.state` (the initial `running` is written by the run-creation
  insert): it applies the §5 run state machine (control edges running↔paused,
  running|paused→stopped; automatic edges running→done, running→failed),
  raises on an illegal transition, updates `updated_at`, and emits
  `run_paused`/`run_resumed`/`run_stopped`/`run_done`/`run_failed` (§7);
  `master_loop` reaches `done`/`failed` through it too. `accept_reduction(conn,
  reduction_id, now)` / `reject_reduction(conn, reduction_id, now)` are the **only**
  writers of `reductions.review_state` for a human decision: they transition it
  `pending → accepted` / `pending → rejected` (raising if it is not `pending`,
  since `accepted`/`rejected`/`superseded` are already resolved), and, for **every**
  ticket the reduction routed to `needs_human` (`tickets.reduction_id ==
  reduction_id`, §4, still in state `needs_human`), transition that ticket
  `needs_human → done` on accept / `needs_human → failed` on reject, clearing its
  attention banner and emitting `reduction_accepted`/`reduction_rejected` plus the
  per-ticket transition events. `requeue_needs_human(conn, ticket_id, now)` is the
  **operator requeue** path for a `needs_human` ticket that was routed by the §3
  master re-verify override or a tripped guard (no reduction to decide): it
  transitions `needs_human → queued` as a fresh attempt (no `attempts` penalty) and
  emits `ticket_requeued`.
- **leases.py** — `acquire(conn, run, resource_class, ticket, host, now)` returns
  a lease iff the number of live (unexpired) leases in `resource_class` is below
  that class's **capacity**, else `None` (caller parks, below). **Class capacity** =
  the sum of `resources_json[resource_class]` over crew members currently `idle`/
  `busy` (the per-host counts a site reports in `HealthReport.resources`, e.g.
  LocalSite `cpu` = `os.cpu_count()`): `resource_classes()` names the classes, the
  crew rows supply their counts (DESIGN §9 "class name + count + semaphore").
  `release(conn, lease, now)` frees a lease; the ticket's lease is released on
  **every** exit from `running`, not just the terminal/reducing ones, so a scarce
  class is returned to the pool immediately rather than lingering a full TTL/backoff:
  `record_result` releases it on `running→reducing`/`failed`/`needs_human` **and** on
  the infra-retry `running→queued` (via `requeue`); the no-penalty transport
  `running→queued` releases it via `requeue_transport`. `renew(conn,
  lease, now)` on the heartbeat sweep; `reclaim_expired(conn, now)` frees leases past
  `expires_at` and requeues **only their still-non-terminal tickets** (a
  `dispatched`/`running` ticket → `queued`, no attempt penalty; a lease whose ticket
  is already terminal or gone is simply freed, never requeued). Every `release` /
  `reclaim_expired` (and each heartbeat sweep) then calls `queue.unpark_ready` for
  the freed class. TTL default 1800s ≫ 30s heartbeat.
- **crew.py** — `add(conn, site, host)` = `provision` + `health`; admit only if
  `health.ok`, else raise with the failing checks; `heartbeat_sweep(conn, site,
  now)` re-probes every host every `HERMES_HEARTBEAT_S` (default 30), updates
  `health_json`/`state`, requeues tickets of hosts gone `down`, renews leases,
  reclaims expired ones, re-admits recovered hosts, and un-parks tickets of any
  class that regained capacity (`queue.unpark_ready`); `drain`/`remove`.
- **drivers.py** — `build_argv(envelope)` → `["claude","-p", prompt,
  "--permission-mode","bypassPermissions", ...]` where `prompt` sets the goal via
  `/goal <goal>` and invokes the methodology `driver.command` if present;
  `parse_result(text_or_file)` → Result. `timeout_s` enforced by the transport's
  `timeout` wrapper (no `--max-turns`).
- **transport.py** — `local_transport(envelope, host)` runs the worker on this
  box; `ssh_transport(host)` scp envelope + ssh run + scp result/evidence back;
  `serve_once_for_host(conn, host, site, ...)` claims one ticket, acquires a
  lease (**if `acquire` returns `None` the class is at capacity: `park_ticket` the
  claimed ticket and return, no dispatch**), builds+validates the envelope, runs via
  the site, records the result;
  envelope/validation errors ⇒ requeue with penalty, transport errors ⇒ requeue
  without penalty (host→down). When building the envelope it **computes**
  `payload_sha256` as the SHA-256 hex digest of the payload's canonical
  (sorted-key, no-whitespace) JSON encoding (§6) and stamps it into the envelope;
  the worker (`mock_agent` in tests, the real driver in production) **recomputes**
  the digest over the received `payload` and, on mismatch, returns a Result with
  `outcome=driver_failed` / `termination_reason=contract_fail` (§6) — no retry.
- **dispatch.py** — `serve_loop(conn, site, host)` repeatedly calls
  `serve_once_for_host`; `master_loop(conn, run, playbook, site)` runs the
  heartbeat sweep (health re-probe, down-requeue, lease renew/reclaim) **every
  cycle regardless of run state**, but performs **all run progression only while
  `run.state == running`** (§5 pause freeze): while a run is
  `paused`/`stopped`/`done`/`failed` it does no `reduce`, no phase advancement, no
  `seed`, and no automatic run→`done`/run→`failed` transition, and a resumed run
  picks progression back up on the next cycle. When `running`, it drives phase
  advancement (when no phase-N ticket is still
  `queued`/`dispatched`/`running`/`parked`/`needs_human` — every one has settled
  into `reducing` or `failed`; a `parked` ticket can still return to `queued` when a
  lease frees and a re-verify/guard-routed `needs_human` ticket can still be
  operator-requeued, so both block advancement → `playbook.reduce` writes
  `reductions` → `playbook.next_phase(run)`; if it
  returns a phase, set `run.phase` to it and call `playbook.seed(run, site)` to
  seed that phase's tickets; if it returns `None`, evaluate `is_done`). A
  `Reduction` returned by `reduce` may carry `needs_human_ticket_ids` (the phase's
  tickets whose final disposition its accept/reject decides); for such a reduction
  `master_loop` persists it `review_state=pending`, routes each listed ticket
  `reducing → needs_human` with `tickets.reduction_id` set to the new reduction's
  id, and emits `needs_human` + `attention`; its later accept/reject (queue.py,
  above) settles them. Every other `reducing` ticket of the phase (not flagged)
  transitions `reducing → done` once `reduce` completes. `master_loop`
  sets `run.state` only through `set_run_state`: `done` when
  `playbook.is_done(run)`, and `failed` when
  the run is stuck (no ticket is `queued`/`dispatched`/`running`/`reducing`/
  `parked`/`needs_human` — every ticket is `done` or `failed` —
  `next_phase` is `None`, and `is_done` is `False`; a `dispatched` ticket is still
  in flight and a `needs_human` ticket still awaits a human, so neither is stuck).
  Because DESIGN froze
  `seed(run, site)` without a findings/reductions argument, `master_loop` passes a
  `Run` snapshot carrying `run.phase` (the phase to seed) and `run.reductions` (the
  prior phase's `reductions`, empty for phase 0) so `seed` can build phase-N
  tickets from phase-(N-1) output; `seed` reads no other engine state.

---

## 10. CLI (`hermes`)

- `hermes run <playbook> --site <site> [--base-ref R] [--hosts a,b] [--dry-run]`
  — create a run, seed phase 0, add the given hosts (defaulting to the local host
  for the `local` site when `--hosts` is omitted), and start the master loop. For
  every host served **in-process** (the `local` site's single box) it **also
  starts an in-process `serve_loop` per such host**, so a single
  `hermes run --site local` without `--dry-run` actually claims and executes
  tickets and drives the run to a terminal state (AC2, §13); on a distributed
  site, remote worker boxes run their own `hermes serve` (below) instead.
- `hermes run {pause|resume|stop} <run_id>` — apply a run control action via
  `queue.set_run_state` (§5, §9); prints the resulting `runs.state` and errors on
  an illegal transition (e.g. resume of a terminal run).
- `hermes reduction {accept|reject} <reduction_id>` — apply the human decision via
  `queue.accept_reduction`/`reject_reduction` (§9): transitions the reduction
  `pending → accepted`/`rejected` and settles every ticket it routed to
  `needs_human` (`→ done` on accept, `→ failed` on reject). Errors (no-op) if the
  reduction is not `pending`.
- `hermes ticket requeue <ticket_id>` — operator requeue of a re-verify/guard-routed
  `needs_human` ticket via `queue.requeue_needs_human` (§9): `needs_human → queued`
  as a fresh attempt (no `attempts` penalty).
- `hermes serve --host <h> --site <site>` — run one host's serve loop (used on a
  worker box / by `add_worker`).
- `hermes crew {add|drain|remove|list} [host] --site <site>` — crew mgmt; `add`
  prints the health check result and admits only if healthy.
- `hermes status [--run R] [--watch]` — render run/ticket/crew/lease/attention
  summary from `queue.db` (pull-based, mirrors the future SPA).
- `hermes show <ticket_id>` — envelope, result, attempts, evidence.

All commands are thin wrappers over the engine modules; `--dry-run` seeds +
reports + estimates without dispatching.

---

## 11. Safety (no-ship, by construction)

- The `local` (and every) site installs PATH guard shims shadowing land/push
  (`git push`, and the ported `sl/jf/arc/hg` land shims) that log + exit non-zero.
- Workers run `--permission-mode bypassPermissions`; safety comes from the guard
  + the no-land invariant, not from permission prompts.
- The engine rejects a dispatch whose `guardrails.no_ship` is true when
  `site.guarantees_no_ship()` is false, and gates host admission on
  `guard_installed` (a `Check`).
- `playbook.verify` re-checks any `ok` result independently (through the site);
  a contradicted result routes to `needs_human`. The engine never trusts a
  worker's success claim on schema-validity alone.

---

## 12. Testkit (mock agent) & test strategy

- **`testkit/mock_agent.py`** — a fake worker invoked by `LocalSite.run_worker`
  in tests (selected via `HERMES_MOCK_AGENT=1`): reads `envelope.json`,
  **recomputes `payload_sha256` over the received `payload` and returns
  `contract_fail` on mismatch** (§6), otherwise per a scenario table writes a
  deterministic `result.json` (ok / contract_fail / driver_error / timeout /
  infra_failed). Lets integration tests exercise the full pipeline with **no real
  `claude`, no SSH, no Meta**.
- **`testkit/example_playbook.py`** — `EchoPlaybook` (registered `name="example"`,
  matching acceptance criterion 2's `hermes run example`): phases
  `["work","reduce"]`, trivial payload/result schemas, `seed` from a canned issue
  file, `reduce` that clusters findings by a field and — when `run.config`
  requests it — returns a reduction carrying `needs_human_ticket_ids` (to exercise
  the reduce→needs_human→accept/reject path, §5/§9), `verify` returning True by
  default (and False under a config flag, to exercise the re-verify→needs_human→
  operator-requeue path).

**Unit tests** (pytest) — one module each: migrations idempotency; contract
validator (accept/reject incl. `additionalProperties`); ticket state-machine
transitions (table-driven over §5, incl. retry cap + backoff + no-penalty transport
path); run state-machine transitions via `set_run_state` (control + terminal edges,
illegal edge raises); reduction resolution (`accept_reduction`/`reject_reduction`
settling a linked `needs_human` ticket, `requeue_needs_human` → `queued`);
lease acquire/renew/reclaim + semaphore; crew add health-gate + heartbeat down/
recover; drivers argv construction; transport command construction
(`ssh_transport` builds the scp-envelope / ssh-run / scp-result-back argv and
maps a non-zero ssh exit to a `transport_error` Result, `subprocess` mocked — no
real SSH); events emit/since ordering; queue claim atomicity (concurrent claim
yields distinct tickets).

**Integration tests** — full pipeline on `LocalSite` + mock agent + EchoPlaybook:
seed → dispatch → run → reduce → advance → done, asserting terminal states, the
event stream contents, contract enforcement (a bad envelope/result NO-GOs), the
no-ship guard actually blocks a `git push`, and the reduce → reduction →
accept/reject path. A "dry-run" GO/NO-GO test asserts a contract mismatch aborts.

`scripts/run_tests.sh` runs unit + integration and prints ALL GREEN / failures.

---

## 13. Acceptance criteria

1. `run_tests.sh` is green: every module unit-tested; the end-to-end integration
   test drives EchoPlaybook to `done` on `LocalSite` with the mock agent.
2. `hermes run example --site local --dry-run` seeds + reports without
   dispatching; without `--dry-run` it drives a run to a terminal state locally.
3. The no-ship guard blocks a push attempt in a worker context (asserted).
4. A malformed envelope or result aborts with a `ContractError` and NO-GO
   (asserted), never a silent pass.
5. Engine core imports zero third-party packages at runtime (asserted by a test
   that scans imports).
6. `crew add` admits only a healthy host and reports each failing `Check`.

---

## 14. Open items (deferred to implementation, not blocking)

- Concurrency: two writers (serve loop + CLI) rely on WAL + `BEGIN IMMEDIATE`;
  the claim test asserts no double-claim under threads. Multi-process serve loops
  on one box are supported via row-level `dispatched` marking.
- `run.state` transitions (running/paused/stopped/done/failed) and stopped-run
  dispatch halting are specified in §4/§5 here (resolving the umbrella deferral).
