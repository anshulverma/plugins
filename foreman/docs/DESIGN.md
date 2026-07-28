# Foreman — design (umbrella)

Status: **draft**. Date: 2026-07-28.

Foreman is a generic engine for running **multi-agent Claude Code work across a
fleet of remote hosts**. It is the successor to `test-fix-harness`, redesigned to
cleanly separate three concerns that were fused together in the original:

1. **How to fan work across many hosts** (the engine),
2. **What job you are doing** (playbooks — test-fixing, training-efficiency, SEV
   root-causing, …),
3. **What environment you are doing it in** (site adapters — the Meta devserver,
   or a plain local box).

This document is the umbrella design. Each sub-project (engine core, playbooks,
site adapters, control plane) gets its own spec + plan under `docs/specs/`.

---

## 1. Goals

Mapped from the revamp request:

| # | Goal | How this design meets it |
|---|------|--------------------------|
| 1 | Clever rename | `foreman` (engine) musters a **crew** of hosts and hands out **tickets**; playbooks are trades: **mechanic** (test-fix), **rigger** (training-eff), **medic** (SEV-RCA). |
| 2 | Separate harness-from-application | Three extension axes: engine / playbook / site. The engine knows nothing about tests. |
| 3 | Friendlier + web UI | React/Vite control-plane SPA (see `web/UI_BRIEF.md`) over a FastAPI JSON API; a mirrored CLI. |
| 4 | Easy to add a host + health check | `foreman crew add <host>` (or a UI button) runs the site's provision + a structured **health probe**; only healthy hosts are admitted, and health is re-probed on a heartbeat. |
| 5 | Richer status | Generic event stream + JSON API + SPA (kanban, crew health, live feed, drill-downs, attention banners). |
| 6 | Separate Meta terminology/tools | **All Meta-isms live in the `meta` site adapter**, injected at deploy time. The engine and playbooks are site-agnostic. |
| 7 | Reusable for different multi-agent work | Playbooks are pluggable; ships **mechanic** + **rigger**, designed to also fit **medic**. |

Plus two requirements added during design:

- **Tests for everything** — unit + integration + e2e, runnable with no Meta
  dependency via the `local` site + a mock agent.
- **Best use of Claude** — workers pursue tickets autonomously using high-level
  Claude Code commands as **drivers** (`/goal`, `/loop`, `/auto-research`, …).
  See §8.

---

## 2. Vocabulary

- **Crew** / **crew member** — the pool of remote hosts, and one host in it.
- **Ticket** — one unit of work (a state, a phase, a resource requirement, a goal).
- **Run** — one invocation of a playbook over a batch of tickets.
- **Playbook** — a job-type's methodology (mechanic / rigger / medic).
- **Driver** — the Claude Code command/skill a worker runs to pursue a ticket
  autonomously (see §8).
- **Finding** / **Reduction** — a worker's structured result, and the master-side
  deduped/aggregated conclusion over many findings.
- **Lease** — a claim on a scarce resource (e.g. a GPU).
- **Site** — the environment adapter that supplies the tools and hosts.

---

## 3. Architecture — three extension axes + drivers

The central idea: **separate methodology (what) from tools (how here) from
fan-out (running it across a crew), and let workers pursue goals via Claude's own
autonomous commands.**

```
                       ┌──────────────────────────────────────────┐
                       │                FOREMAN ENGINE             │
                       │  queue · dispatch · transport · crew +    │
                       │  health · leases · contracts · events ·   │
                       │  control-plane API · web UI · CLI         │
                       │                                            │
                       │   loads   ┌───────────┐   ┌───────────┐   │
                       │  ───────▶ │ PLAYBOOK  │   │   SITE    │ ◀─│── injected at
                       │           │ interface │   │ interface │   │   deploy time
                       └───────────┴─────┬─────┴───┴─────┬─────┴───┘
                                         │               │
                    methodology (what) ──┤               ├── tools + hosts (how here)
                                         │               │
                       mechanic · rigger · medic     local · meta
```

- **Engine (`foreman`)** — generic. Owns the queue, dispatch, transports, crew
  registry + health, leases, contracts, the event stream, the control-plane API,
  the web UI, and the CLI. Contains **no** test/SEV/Meta concepts.
- **Playbook** — declares: how to **seed** tickets, the ticket **payload +
  result** sub-schemas, the **driver** per phase, the master-side **reduce**
  step, and the **definition-of-done**. Site-agnostic: it calls environment
  capabilities through the site interface, never `buck2`/`sl`/`testx` directly.
- **Site adapter** — supplies the **environment primitives**: host
  discovery/provisioning, the remote-exec recipe, VCS/submit, issue/metric
  sources, resource classes (GPU/RE), the no-ship guard, and per-host **health
  probes**. **This is the only place Meta-isms live.** The engine ships a `local`
  site (localhost + git + shell) so the whole system runs and is fully testable
  on a plain dev box; the `meta` site is the devserver reality.

### The two interfaces (extension points)

```python
# engine/playbook.py
class Playbook(Protocol):
    name: str
    phases: list[str]                     # e.g. ["diagnose", "reduce", "fix"]
    def seed(self, run, site) -> list[Ticket]: ...
    def payload_schema(self, phase: str) -> dict: ...   # JSON-schema subset
    def result_schema(self, phase: str) -> dict: ...
    def driver(self, phase: str) -> Driver: ...          # which Claude command drives it
    def reduce(self, run, phase, findings, site) -> list[Reduction]: ...
    def next_phase(self, run) -> str | None: ...         # phase advancement
    def is_done(self, run) -> bool: ...                  # definition-of-done

# engine/site.py
class Site(Protocol):
    name: str
    def discover_hosts(self) -> list[str]: ...           # optional auto-enumeration
    def provision(self, host, base_ref) -> None: ...     # idempotent
    def health(self, host) -> HealthReport: ...          # §7
    def run_worker(self, host, envelope) -> Result: ...  # the remote-exec recipe
    def resource_classes(self) -> list[str]: ...         # e.g. ["cpu","gpu"]
    def submit_for_review(self, host, change) -> str: ... # returns a review URL; never lands
    def issue_source(self, query) -> list[dict]: ...      # e.g. failing-test dashboard
```

Both interfaces are small on purpose: a new site or playbook is one file
implementing a handful of methods, and each is unit-testable in isolation.

---

## 4. Component map & repo structure

Each is a self-contained plugin (this repo's convention). The engine **core**
stays **stdlib-only** (dotsync-safe, like dexter's `kb.py`); the control-plane
**server** uses FastAPI; the **UI** is React/Vite/TS.

```
plugins/
  foreman/                       # ENGINE plugin
    .claude-plugin/plugin.json
    commands/                    # /foreman:run · :status · :crew · :serve
    skills/foreman/SKILL.md
    engine/                      # stdlib-only python package
      db/  (schema.sql, migrate.py)
      queue.py     dispatch.py   transport.py
      crew.py      leases.py     contracts.py    events.py
      drivers.py                 # Driver model + headless-claude invocation helpers
      playbook.py  site.py       # ABCs + loaders (extension points)
      cli.py
    server/                      # FastAPI JSON API + websocket event feed
    web/                         # React/Vite SPA  (see web/UI_BRIEF.md)
    sites/local/                 # reference site: localhost + git + shell
    testkit/                     # mock agent runner + fixtures (shared by tests)
    docs/  (DESIGN.md, specs/…)
    tests/{unit,integration,e2e}
  mechanic/                      # test-fix playbook (depends on foreman)
    playbook/ (seed, phases, prompts/, reduce, done, schema)
    commands/ (/mechanic:fix, :status)     tests/
  rigger/                        # training-efficiency playbook              tests/
  foreman-site-meta/             # THE Meta site adapter (deploy-time)
    site/ (od hosts, ssh recipe, buck2/sl/jf, testinfra, gpu/re, guards, health)
    tests/
```

**Runtime data** (queue.db, logs, ticket payloads/evidence) lives **outside the
repo** under `FOREMAN_HOME` (default `~/.foreman`), mirroring dexter's
code-vs-runtime-data split and today's `~/.tfh`. The engine and scripts own all
reads/writes to it; nothing hardcodes a user path (the original hardcoded
`/data/users/anshulverma/...` — that becomes site config).

---

## 5. Data model (generic core)

No test/SEV concepts in the core schema. SQLite, WAL, mode 0600, master-owned,
additive-only migrations (ported discipline from `schema.sql`).

- `runs` — id, playbook, site, config_json, base_ref, state, phase, started_at.
- `tickets` — id, run_id, phase, state, resource_req, priority, attempts,
  available_at, lease_expires_at, payload_ref, worker_host, tried_hosts.
- `attempts` — append-only audit per execution (host, started/ended, outcome,
  termination_reason, result_ref, error_summary).
- `crew` — id (host), site, capabilities, resources_json, state
  (idle/busy/down/draining), **health_json**, last_heartbeat, current_ticket,
  manifest_sha.
- `leases` — id, resource_class, holder_ticket, host, acquired_at, ttl_s,
  expires_at.
- `events` — append-only feed: ts, kind, run_id, ticket_id, host, message,
  data_json (drives the live UI feed + `foreman status`).
- `findings` — generic JSON-doc store: run_id, ticket_id, kind, json (a
  playbook interprets its own `kind`s — root_cause, metric_sample, …).
- `reductions` — master-side aggregate output: run_id, kind, json, review_state.

Ticket states: `queued · dispatched · running · reducing · done · parked ·
failed · needs_human`. Every execution outcome is terminal; retries increment
only on **infra** failure (ported invariant, max 3).

Playbook-specific structure never grows the core schema — it lives as namespaced
`findings`/`reductions` documents. This keeps the engine truly generic (goal #2).

---

## 6. Contracts

The strict `additionalProperties:false` discipline from `contracts.py` is
preserved (dependency-free validator ported verbatim). Contracts are layered:

- **Engine envelope** (fixed shell): `ticket_id, run_id, phase, resource_req,
  base_ref, payload_sha256, timeout_s, site_context, goal, driver`.
- **Playbook sub-schemas**: the playbook contributes the `payload` (inside the
  envelope) and the `result` schema for each phase. The engine validates both
  the envelope and the playbook sub-schemas on dispatch and on result.
- **GoalEnvelope** (new, §8): `goal` (definition-of-done text), `driver`
  (`{command, args}`), `done_contract` (the required result schema),
  `guardrails` (no-ship, budget/turn caps, timeout).

A contract mismatch in either direction is a hard error (the dry-run NO-GO gate
is ported).

---

## 7. Crew: provisioning, health, add-a-host (goal #4)

Adding a host is one command or one UI button; the site adapter encapsulates the
"how."

```
foreman crew add <host>
  → site.provision(host, base_ref)      # idempotent: workspace, agent, guard, warm caches
  → report = site.health(host)          # structured probe
  → admit iff report.ok, else show exactly which checks failed
```

`HealthReport` (structured, rendered as `HealthBadge` in the UI):

```python
@dataclass
class HealthReport:
    reachable: bool          # transport connects
    agent_ok: bool           # headless Claude present + correct version
    auth_ok: bool            # `claude -p ping` authenticates
    workspace_ready: bool    # checkout at base_ref, clean
    guard_installed: bool    # no-ship shims earlier on PATH (§12)
    resources: dict          # {"gpu": 8, "cpu": 96}
    latency_ms: int
    checks: list[Check]      # named sub-checks with pass/fail + detail
    @property
    def ok(self) -> bool: ...
```

The daemon re-probes health on a heartbeat; a member that fails goes `down`,
its in-flight ticket is requeued (transport failure ⇒ no attempt penalty, ported
semantics). This replaces the original's stubbed `verify_worker.sh` /
`bootstrap_worker.sh` with a real, per-site, structured probe.

---

## 8. The driver model — making best use of Claude (`/goal`, `/loop`, …)

**Foreman is a goal dispatcher, not a prompt templater.** Instead of shipping a
bespoke prompt, foreman hands each crew member a **GoalEnvelope** and a
**driver** — a high-level Claude Code command/skill that pursues the goal
autonomously and to completion. The worker prompt becomes thin: set up the goal +
invoke the driver + emit the strict result contract.

```python
@dataclass
class Driver:
    command: str          # e.g. "/goal", "/auto-research", "/mp-diagnose"
    args: dict            # command-specific
    max_turns: int | None # autonomous-run cap
    loop: str | None      # optional /loop interval, e.g. "10m", for polling drivers
```

**Why:** these commands already encode disciplined, autonomous loops (diagnose →
reproduce → fix → verify; experiment → measure → keep/discard). Reusing them
means foreman gets Claude's best autonomous behavior for free and stays out of
the business of re-implementing methodology in prompt text.

**Driver-per-phase, chosen by the playbook** (`Playbook.driver(phase)`). The
engine treats `driver.command` as **opaque** — the catalog below is a starting
point, configurable per deployment, and can grow without engine changes.

| Playbook | Phase | Driver | Status |
|----------|-------|--------|--------|
| mechanic | diagnose | `/mp-diagnose` or `/testx-debug` | both available as skills |
| mechanic | fix | `/ci-autopilot` (drive to green + publish), optionally `/divine` | confirmed |
| rigger | optimize | `/auto-research` (experiment loop vs. a metric) | confirmed |
| medic | rca | `/divine` or `/mp-diagnose` (multi-phase RCA) | confirmed |

**Confirmed autonomous drivers** available in this environment: `/auto-research`,
`/divine`, `/ci-autopilot`, `/ci-patrol`, `/mp-diagnose`, `/testx-debug`.

`/loop` (confirmed) runs a prompt/command on a **recurring interval** and is
*interactive, not autonomous-to-completion*. So `/loop` is a **master-side**
tool (re-probe crew health, watch a run), **not** a worker-completion driver.
`Driver.loop` remains available for polling-style workers, but the primary
worker drivers are the autonomous ones above.

> ⚠️ **`/goal` is unconfirmed** — it is not a documented built-in and was not
> found in the installed command/skill set, so it is likely a custom command in
> your environment. The design therefore does **not depend** on `/goal`; it is
> supported only as one more opaque `driver.command` value if/when present.

---

## 9. Scheduling & leases

Generic resource leases (not GPU-specific): a ticket declares `resource_req`
(a resource class the site defines, e.g. `cpu`/`gpu`); the scheduler leases a
matching, healthy crew member. Scarce classes sit behind a semaphore; overflow
**parks** (ported behavior). GPU/RE specifics live entirely in the `meta` site's
`resource_classes()` — the engine only knows "class name + count + semaphore."

---

## 10. Control plane & status (goals #3, #5)

- **API** (`server/`, FastAPI): REST for runs/tickets/crew/health/leases/
  findings + a **websocket** feed backed by the `events` table. Control actions:
  start/resume/stop run, add/drain/remove host, requeue/reprioritize/park ticket,
  ack banner.
- **UI** (`web/`, React/Vite SPA): generated from `web/UI_BRIEF.md` by Claude
  Design. Screens: run overview, ticket kanban, ticket drill-down, crew panel
  (health + add-host modal), findings, live feed; light+dark; attention banners.
- **CLI** mirrors it: `foreman status [--watch]`, `foreman crew`,
  `foreman show <ticket>`.

---

## 11. Safety (ported invariant: nothing auto-ships)

Enforced **by construction**, not prompt trust: the site installs PATH shims that
shadow land/push/submit-and-land on workers (ported `land_guard.sh`), workers use
a submit-only identity, and the master re-verifies any "green"/"success" claim
independently. `site.submit_for_review` returns a review URL and can never land.
`guard_installed` is a health-gate check (§7).

---

## 12. Testing strategy (first-class requirement)

- **Unit** — every engine module (queue state machine, lease scheduler, contract
  validator, crew/health parsing, event log, transport payload plumbing, driver
  envelope construction); each playbook's pure logic (seed/reduce/done); each
  site adapter (command construction + health parsing, subprocess mocked).
  Python: `pytest`; frontend: `vitest` + React Testing Library.
- **Integration** — the **full pipeline on the `local` site with a mock agent
  runner** (a fake `claude` that reads a payload and writes a canned result):
  seed → dispatch → run → reduce → done, asserting terminal states, contract
  enforcement, the event stream, and that the **no-ship guard actually blocks**.
  Plus control-plane API tests (spin the server, hit endpoints + websocket).
- **E2E** — Playwright drives the web UI against a seeded local run.
- A top-level `run_tests.sh` runs all suites; each plugin owns its `tests/`.
  Everything runs with **no Meta dependency** thanks to the `local` site.

---

## 13. Decomposition & sequencing

~4 sub-projects; each gets a spec + plan under `docs/specs/`:

1. **Engine core** (+ `local` site + `testkit` mock agent + full unit/integration
   tests) — the foundation. **Spec next.**
2. **mechanic playbook + `meta` site adapter** — first real job-type end-to-end.
3. **Control-plane server + React SPA wiring** — status + control (UI generated
   in parallel via `web/UI_BRIEF.md`).
4. **rigger playbook** — validates genericity (different shape from mechanic).
5. **medic** — designed-for; stub/optional.

Ordering is adjustable; the engine core must land first.

---

## 14. Open questions & spikes

- **SPIKE (blocking, do first): headless slash-command invocation.** The entire
  driver model depends on being able to run a named slash command (e.g.
  `/mp-diagnose …`) in a fully non-interactive `claude -p` / Agent-SDK run, to
  completion, with the right permission mode + turn cap + structured output. This
  is **currently unconfirmed** (Agent SDK / `claude -p` docs). Prove it end-to-end
  with one driver on the `local` site before building `site.run_worker`. If a
  slash command can't be passed headlessly, the fallback is to inline the skill's
  content into the worker prompt (drivers become prompt-fragments, not commands) —
  the `Driver` abstraction absorbs either outcome.
- `/goal` availability/semantics — treat as an optional custom command; do not
  depend on it (see §8).
- FastAPI is an added dependency for `server/` — acceptable, given the engine
  core stays stdlib-only? (Assumed yes per the "Full SPA" choice.)
- `meta` site adapter: ship it in this repo as the reference, or keep it in a
  separate private location and load via config? (Leaning: reference impl here,
  loaded via `FOREMAN_SITE=meta`.)
