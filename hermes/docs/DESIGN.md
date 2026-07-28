# Hermes — design (umbrella)

Status: **draft**. Date: 2026-07-28.

Hermes is a generic engine for running **multi-agent Claude Code work across a
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
| 1 | Clever rename | `hermes` (engine) musters a **crew** of hosts and hands out **tickets**; playbooks are trades: **mechanic** (test-fix), **rigger** (training-eff), **medic** (SEV-RCA). |
| 2 | Separate harness-from-application | Three extension axes: engine / playbook / site. The engine knows nothing about tests. |
| 3 | Friendlier + web UI | React/Vite control-plane SPA (see `web/UI_BRIEF.md`) over a FastAPI JSON API; a mirrored CLI. |
| 4 | Easy to add a host + health check | `hermes crew add <host>` (or a UI button) runs the site's provision + a structured **health probe**; only healthy hosts are admitted, and health is re-probed on a heartbeat. |
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
                       │                HERMES ENGINE             │
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

- **Engine (`hermes`)** — generic. Owns the queue, dispatch, transports, crew
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
    def verify(self, run, ticket, result, site) -> bool: ...  # master-side independent
                             #   re-verify of a goal_met/ok result (§3, §11): re-checks the
                             #   worker's success claim through the site (not just schema
                             #   validation). True ⇒ admit to reducing/done; False ⇒ route to
                             #   needs_human. Default True for phases with nothing to re-check.
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
    def issue_source(self, query: IssueQuery) -> list[Issue]: ...  # e.g. failing-test dashboard
    def guarantees_no_ship(self) -> bool: ...            # can this site install the no-ship guard? (§6, §11)
```

The value types the `Site` interface returns/accepts (dataclasses; `HealthReport`
is defined in §7):

```python
# engine/site.py (cont.)

@dataclass
class Result:                 # returned by Site.run_worker
    outcome: str             # "ok" | "driver_failed" | "infra_failed"
    termination_reason: str  # "goal_met" | "contract_fail" | "driver_error"
                             #   | "timeout" | "transport_error"
    result_ref: str | None   # handle to the worker's emitted result doc (validated
                             #   against the phase's done_contract); None if none produced
    error_summary: str | None
    started_at: float        # epoch seconds
    ended_at: float          # epoch seconds

@dataclass
class IssueQuery:            # argument to Site.issue_source
    kind: str               # which issue class to fetch, e.g. "failing_test"
    filters: dict           # site-documented key/value narrowing (default {})
    limit: int              # max issues to return (default 100)

@dataclass
class Issue:                 # one item returned by Site.issue_source
    id: str                 # site-stable identifier (e.g. a failing-test name)
    kind: str               # echoes IssueQuery.kind, e.g. "failing_test" | "sev"
    title: str              # human-readable summary
    ref: str                # URL or path back to the source of record
    data: dict              # site-specific extras (owner, signal, first-seen, …)
```

**`termination_reason` → `outcome` → disposition** (the mapping is total over the
`termination_reason` enum; §5 consumes `outcome`):

| `termination_reason` | `outcome` | Disposition (§5) |
|----------------------|-----------|------------------|
| `goal_met` | `ok` | Ticket → `reducing`/`done`, **subject to master re-verify** (§11). |
| `contract_fail` | `driver_failed` | **Terminal, no retry** (`failed`). |
| `driver_error` | `driver_failed` | **Terminal, no retry** (`failed`). |
| `timeout` | `driver_failed` | **Terminal, no retry** (`failed`). A `timeout_s` blow-out is treated as a driver failure, not infra: re-running the same driver on the same input under the same budget is not expected to change the outcome, so it does **not** consume an infra retry. |
| `transport_error` | `infra_failed` | **Retried up to 3×** (§5); the 4th → `failed`. |

**Master re-verify override:** an `outcome == "ok"` / `goal_met` result whose
independent master-side re-verify (§11) **contradicts** the worker's success claim
is not admitted as done. The ticket is routed to **`needs_human`** (an integrity
signal — the worker asserted success the master could not confirm — that warrants
inspection rather than a silent retry). This is the only path by which an `ok`
result does not reach `done`.

Both interfaces are small on purpose: a new site or playbook is one file
implementing a handful of methods, and each is unit-testable in isolation.

---

## 4. Component map & repo structure

Each is a self-contained plugin (this repo's convention). The engine **core**
stays **stdlib-only** (dotsync-safe, like dexter's `kb.py`); the control-plane
**server** uses FastAPI; the **UI** is React/Vite/TS.

```
plugins/
  hermes/                       # ENGINE plugin
    .claude-plugin/plugin.json
    commands/                    # /hermes:run · :status · :crew · :serve
    skills/hermes/SKILL.md
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
  mechanic/                      # test-fix playbook (depends on hermes)
    playbook/ (seed, phases, prompts/, reduce, done, schema)
    commands/ (/mechanic:fix, :status)     tests/
  rigger/                        # training-efficiency playbook              tests/
  hermes-site-meta/             # THE Meta site adapter (deploy-time)
    site/ (od hosts, ssh recipe, buck2/sl/jf, testinfra, gpu/re, guards, health)
    tests/
```

**Runtime data** (queue.db, logs, ticket payloads/evidence) lives **outside the
repo** under `HERMES_HOME` (default `~/.hermes`), mirroring dexter's
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
  data_json (drives the live UI feed + `hermes status`).
- `findings` — generic JSON-doc store: run_id, ticket_id, kind, json (a
  playbook interprets its own `kind`s — root_cause, metric_sample, …).
- `reductions` — master-side aggregate output: run_id, kind, json, review_state.
  `review_state` ∈ `pending · accepted · rejected · superseded`: `pending` (newly
  emitted by `reduce`, awaiting a human/master decision — raises an attention
  banner when the reduction is what routed a ticket to `needs_human`); `accepted`
  (approved/actioned); `rejected` (dismissed); `superseded` (replaced by a newer
  reduction over the same findings — reductions are never deleted, mirroring the
  append-only discipline).

Ticket states: `queued · dispatched · running · reducing · done · parked ·
failed · needs_human`. **Two failure classes, resolved distinctly** by
`Result.outcome` (§3):

- **Driver-reported (non-infra) failure** (`Result.outcome == "driver_failed"` —
  the driver ran to completion but its result fails `done_contract` validation, or
  the driver explicitly reports it cannot meet the goal): **terminal on the first
  occurrence.** The ticket goes `failed` immediately with **no retry**, because
  re-running the same driver on the same input is not expected to change the
  outcome. `attempts` is **not** incremented for retry purposes here.
- **Infra failure** (`Result.outcome == "infra_failed"` — a worker/host infra
  error surfaced by a completed attempt): **retried up to 3 times** (the ported
  invariant); `attempts` increments only on this class, and the 4th infra failure
  sends the ticket to `failed`.

(A host lost mid-run and reclaimed via the heartbeat/lease path — §7, §9 — is a
*no-penalty requeue*, distinct from the two classes above: it does not increment
`attempts`, since the attempt never produced a `Result`.)

`failed` and `needs_human` **both** raise an attention banner (each demands human
notice). `needs_human` is reserved for tickets a playbook's `reduce`/`is_done`
logic or a tripped guard (§11) flags for a human decision, whereas `failed`
signals exhaustion of automated options. **Resolving a `needs_human` ticket:**
when the ticket was routed there **by a reduction**, the human decision is made
by accepting/rejecting that reduction (§10) — **accept** transitions it
`needs_human → done` (the reduction's conclusion is affirmed and actioned; any
follow-on work is seeded as *new* tickets, never by reopening this one),
**reject** transitions it `needs_human → failed` (no automated conclusion
remains). When the ticket was routed there by the §3 master re-verify override or
a tripped guard (no reduction to decide), an operator clears it with a control
action (§10: `requeue` re-queues it as a fresh attempt, or the banner is
`ack`'d). A ticket's `needs_human` banner is an attribute of the `needs_human`
state and clears the instant the ticket leaves it; the `failed` banner that a
reject produces is the distinct terminal signal (ackable, §10), not the
`needs_human` banner re-raised. `parked` means blocked on a scarce lease
(§9) and is re-queued automatically when a lease frees.

Playbook-specific structure never grows the core schema — it lives as namespaced
`findings`/`reductions` documents. This keeps the engine truly generic (goal #2).

---

## 6. Contracts

The strict `additionalProperties:false` discipline from `contracts.py` is
preserved (dependency-free validator ported verbatim). Contracts are layered:

- **Engine envelope** (fixed shell): `ticket_id, run_id, phase, resource_req,
  base_ref, payload_sha256, timeout_s, site_context, goal_envelope`. `timeout_s`
  is the single wall-clock budget for the worker run (default 3600 s, capped per
  deployment), enforced by the transport's `timeout` wrapper (§14); it is the
  only timeout in the system.
- **Playbook sub-schemas**: the playbook contributes the `payload` (inside the
  envelope) and the `result` schema for each phase. The engine validates both
  the envelope and the playbook sub-schemas on dispatch and on result.
- **GoalEnvelope** (new, §8): the value of the envelope's `goal_envelope` field.
  Fields: `goal` (definition-of-done text, set per ticket by the playbook),
  `driver` (a `Driver`, §8), `done_contract` (the required result schema — the
  playbook's `result_schema(phase)`), `guardrails` — a concrete object
  `{"no_ship": bool}` (default `true`) asserting the no-ship posture the worker
  must run under (submit-only identity + PATH shims, §11). No-ship is enforced at
  two levels: (a) **site-level capability** — the master rejects an envelope with
  `no_ship:true` at dispatch if `not site.guarantees_no_ship()` (§3); (b)
  **per-host guarantee** — only crew members whose health probe reported
  `guard_installed == True` are admitted (§7), and `guard_installed == False` is
  always admission-blocking (§11), so an `no_ship:true` envelope can only ever
  target a host on which the guard was proven installed. **No turn/token/$ budget lives in
  `guardrails`:** this build has no `--max-turns` flag (§14), so the sole worker
  budget is the wall-clock `timeout_s` above.

A contract mismatch in either direction is a hard error (the dry-run NO-GO gate
is ported).

---

## 7. Crew: provisioning, health, add-a-host (goal #4)

Adding a host is one command or one UI button; the site adapter encapsulates the
"how."

```
hermes crew add <host>
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
    guard_installed: bool    # no-ship shims earlier on PATH (§11)
    resources: dict          # {"gpu": 8, "cpu": 96}
    latency_ms: int
    checks: list[Check]      # named sub-checks with pass/fail + detail
    @property
    def ok(self) -> bool: ...  # True iff every Check in `checks` passed

@dataclass
class Check:
    name: str                # stable check id, e.g. "reachable", "guard_installed"
    ok: bool                 # pass/fail
    detail: str              # human-readable reason (shown when it fails)
```

`checks` is the source of truth for admission: `ok` is `True` iff **every** entry
in `checks` passes. The five named booleans (`reachable`, `agent_ok`, `auth_ok`,
`workspace_ready`, `guard_installed`) are **required, convenience mirrors** of the
same-named `Check` entries every site must emit; a site may add further checks
(which also gate `ok`). `guard_installed == False` is always admission-blocking
(§11).

The daemon re-probes health on a heartbeat (default every 30 s, configurable via
`HERMES_HEARTBEAT_S`); a member that fails a probe goes `down`, its in-flight
ticket is requeued (transport failure ⇒ no attempt penalty, ported semantics),
and the member is re-admitted automatically once a later probe passes. This
replaces the original's stubbed `verify_worker.sh` / `bootstrap_worker.sh` with a
real, per-site, structured probe.

---

## 8. The driver model — making best use of Claude (`/goal`, `/loop`, …)

**Hermes is a goal dispatcher, not a prompt templater.** Instead of shipping a
bespoke prompt, hermes hands each crew member a **GoalEnvelope**: a **completion
condition** (delivered via `/goal <condition>`) plus a **methodology driver** —
a high-level Claude Code command/skill that pursues that condition autonomously.
The worker prompt becomes thin: set the goal, invoke the driver, emit the strict
result contract.

`/goal` is the backbone of this model. It sets a **persistent completion
condition** and lets Claude work **autonomously across multiple turns until a
secondary fast model verifies the goal is met** — which is precisely a ticket's
definition-of-done. Sub-commands hermes uses:

- `/goal <condition>` — start the ticket's autonomous pursuit.
- `/goal` — poll status (is the condition met yet?).
- `/goal clear` — stop (on requeue, park, or abort).

```python
@dataclass
class Driver:
    command: str | None   # methodology driver, e.g. "/auto-research", "/mp-diagnose"
    args: dict            # command-specific
    loop: str | None      # optional /loop interval, e.g. "10m", for polling drivers
    # NB: no turn cap here. This build has no --max-turns flag (§14), so the sole
    # worker budget is the envelope's wall-clock timeout_s (§6); a per-phase turn
    # limit would be unenforceable and is deliberately omitted.
    # NB: the completion condition (`goal`) is NOT here — it is per-ticket and
    # lives on the GoalEnvelope (§6); a Driver is per-phase and goal-agnostic.
```

So `/goal` (completion condition) and the methodology command **compose**: e.g.
set `/goal "test X is green and a diff is published"`, then kick off with
`/mp-diagnose`/`/ci-autopilot`. Two layers of verification result — `/goal`'s
worker-side verifier, and hermes's independent master-side re-verify (§11) — and
the no-trust invariant holds.

**Why:** these commands already encode disciplined, autonomous loops (diagnose →
reproduce → fix → verify; experiment → measure → keep/discard). Reusing them
means hermes gets Claude's best autonomous behavior for free and stays out of
the business of re-implementing methodology in prompt text.

**Driver-per-phase, chosen by the playbook** (`Playbook.driver(phase)`). The
per-ticket completion condition (`goal`) is set separately by the playbook at
seed / phase entry from that phase's definition-of-done; the engine then
assembles the ticket's `goal`, the phase `Driver`, the phase `result_schema`
(as `done_contract`), and the site + run `guardrails` into the GoalEnvelope. The
engine treats `driver.command` as **opaque** — the catalog below is a starting
point, configurable per deployment, and can grow without engine changes.

| Playbook | Phase | Driver | Status |
|----------|-------|--------|--------|
| mechanic | diagnose | `/mp-diagnose` or `/testx-debug` | both available as skills |
| mechanic | fix | `/ci-autopilot` (drive to green + publish), optionally `/divine` | confirmed |
| rigger | optimize | `/auto-research` (experiment loop vs. a metric) | confirmed |
| medic | rca | `/divine` or `/mp-diagnose` (multi-phase RCA) | confirmed |

**Confirmed drivers** available in this environment:

- **Completion condition:** `/goal` (backbone; §above).
- **Methodology loops:** `/auto-research` (experiment vs. a metric), `/divine`
  (multi-phase pipeline), `/ci-autopilot` + `/ci-patrol` (drive CI to green),
  `/mp-diagnose` + `/testx-debug` (disciplined diagnosis), `/auto-plan`
  (autonomous planning with **hardening loops** — for planning-heavy phases, or
  master-side to harden a run's plan before seeding tickets).
- **Interval:** `/loop`.

`/loop` runs a prompt/command on a **recurring interval** and is *interactive,
not autonomous-to-completion*. So `/loop` is a **master-side** tool (re-probe
crew health, watch a run), **not** a worker-completion driver — that role belongs
to `/goal` + a methodology loop. `Driver.loop` remains available for
polling-style workers.

The engine treats every `driver.command` as opaque, so this catalog can grow
without engine changes.

---

## 9. Scheduling & leases

Generic resource leases (not GPU-specific): a ticket declares `resource_req`
(a resource class the site defines, e.g. `cpu`/`gpu`); the scheduler leases a
matching, healthy crew member. Scarce classes sit behind a semaphore; overflow
**parks** (ported behavior). A lease carries a TTL (`ttl_s`, default 1800 s) and
is renewed **on the same 30 s crew-health heartbeat cycle** (§7,
`HERMES_HEARTBEAT_S`) while its ticket runs — there is **no** separate lease
timer; the daemon renews every live lease as part of each heartbeat sweep. `ttl_s`
(1800 s) is deliberately ≫ the 30 s heartbeat so a lease survives a few missed
sweeps before expiring. A lease whose holder is unreachable past `expires_at`
(i.e. renewal has stopped for a full TTL) is reclaimed and its ticket requeued
(transport failure ⇒ no attempt penalty), which bounds leaks from a crashed
master or worker. GPU/RE specifics live entirely in the `meta` site's
`resource_classes()` — the engine only knows "class name + count + semaphore."

---

## 10. Control plane & status (goals #3, #5)

- **API** (`server/`, FastAPI): REST for runs/tickets/crew/health/leases/
  findings/reductions + a **websocket** feed backed by the `events` table. Control
  actions: start/resume/stop run, add/drain/remove host,
  requeue/reprioritize/park ticket, **accept/reject reduction**
  (`POST /reductions/{id}/accept` · `POST /reductions/{id}/reject`, transitioning
  `review_state` `pending → accepted`/`rejected` (§5) and emitting a
  `reduction_accepted`/`reduction_rejected` event; only a `pending` reduction is
  transitionable — an accept/reject on an `accepted`/`rejected`/`superseded`
  reduction ⇒ `409`; and accepting/rejecting a reduction that routed one or more
  tickets to `needs_human` (§5) also transitions each such ticket out of
  `needs_human` — `needs_human → done` on accept, `needs_human → failed` on
  reject — clearing that ticket's `needs_human` attention banner (the ticket is no
  longer `needs_human`, so §5's blanket banner rule no longer applies to it; a
  reject's resulting `failed` banner is the distinct terminal signal, not the
  cleared banner re-raised)), ack banner.
- **Auth & binding (required — these actions are destructive and workers run
  `bypassPermissions`).** The server **binds to `127.0.0.1` by default**
  (`HERMES_BIND`, overridable to `0.0.0.0` only behind a trusted proxy). A
  **bearer token** (generated on first `hermes serve`, stored 0600 at
  `$HERMES_HOME/api_token`, rotatable via `hermes serve --rotate-token`) is
  **required on every mutating request** (`POST`/`DELETE`, i.e. all control
  actions above) and on the **websocket handshake** (`?token=` or
  `Authorization` header). Read-only `GET` endpoints are token-gated too whenever
  the bind address is non-loopback. A missing/invalid token ⇒ `401`; the websocket
  closes with code `4401`. Requests without a valid token can never mutate state.
- **Token acquisition per actor.** The **CLI** reads the token directly from
  `$HERMES_HOME/api_token` (same host, 0600 file). The **SPA is served by the
  same FastAPI server** and, being a browser app, has no filesystem access, so it
  obtains the token as follows:
  - **Loopback default (`127.0.0.1`):** the server injects the current token into
    the served `index.html` as a bootstrap value; the SPA holds it **in memory
    only** (never `localStorage`/cookies, to avoid on-disk persistence) for the
    tab's lifetime and sends it on every request/websocket handshake. Injection is
    safe here because reaching the page already requires local access to the
    loopback port.
  - **Non-loopback (`0.0.0.0` behind a trusted proxy):** bootstrap injection is
    **disabled**; the SPA requires an explicit **login step** where the operator
    pastes the token (or the trusted proxy supplies it), again held in memory only.
- **Token lifecycle.** The token is a single shared secret with **no TTL** (it
  does not expire on its own) and **no per-actor scoping/permissions** — every
  holder has full control-plane authority. `hermes serve --rotate-token`
  generates a new token and **immediately invalidates all in-flight sessions**:
  subsequent requests bearing the old token get `401`, and every open websocket
  authenticated with it is closed with code `4401` (clients must re-fetch/re-enter
  the new token; the SPA reloads to pick up a freshly injected value). **0.0.0.0
  caveat:** the single-shared-token, no-per-actor-permission model is acceptable
  only for the loopback single-operator default; a non-loopback deployment must sit
  behind a trusted proxy that supplies its own authentication/authorization, as
  per-actor tokens and scoped permissions are out of scope for this build.
- **UI** (`web/`, React/Vite SPA): generated from `web/UI_BRIEF.md` by Claude
  Design. Screens: run overview, ticket kanban, ticket drill-down, crew panel
  (health + add-host modal), findings, live feed; light+dark; attention banners.
- **CLI** mirrors it: `hermes status [--watch]`, `hermes crew`,
  `hermes show <ticket>`.

---

## 11. Safety (ported invariant: nothing auto-ships)

Enforced **by construction**, not prompt trust: the site installs PATH shims that
shadow land/push/submit-and-land on workers (ported `land_guard.sh`), workers use
a submit-only identity, and the master re-verifies any "green"/"success" claim
independently via `Playbook.verify(run, ticket, result, site)` (§3) — which
re-checks the claim through the site rather than trusting the worker's assertion or
a mere schema-valid `result_ref`, and whose contradicting verdict routes the ticket
to `needs_human` (§3). `site.submit_for_review` returns a review URL and can never land.
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

- **SPIKE — headless slash-command invocation: RESOLVED (2026-07-28).** Verified
  on the `local` box that a named slash command runs in a fully non-interactive
  `claude -p` run and drives real work to completion: `claude -p "/goal <multi-step
  condition>" --permission-mode bypassPermissions` executed the goal and produced
  the correct side effects (exit 0). Design consequences now baked in:
  - Workers invoke `claude -p "/goal <condition>"` (+ methodology driver) with
    **`--permission-mode bypassPermissions`** so the agent can act freely; the
    no-ship guard (§11) — not the permission prompt — is what keeps it safe.
  - **This build has no `--max-turns` flag**, so there is no turn-based limiting at
    all; the sole worker budget is the envelope's wall-clock `timeout_s` (§6),
    enforced by a `timeout` wrapper at the transport layer (ported from the
    original `run_unit.sh`). `Driver` therefore carries no `max_turns` field.
  - Fallback if a future driver can't be passed as a slash command: inline the
    skill's content into the worker prompt (drivers become prompt-fragments) — the
    `Driver` abstraction absorbs either outcome.
- **RESOLVED — FastAPI dependency (2026-07-28).** `server/` depends on FastAPI;
  the engine **core** (`engine/`) stays strictly stdlib-only, so a deployment
  that only needs the CLI never imports FastAPI. The dependency is isolated to
  the control-plane server (§4, §10).
- **RESOLVED — `meta` site location (2026-07-28).** The `meta` adapter ships **in
  this repo** as the reference implementation under `hermes-site-meta/` (§4) and
  is selected at deploy time via `HERMES_SITE=meta` (default `local`). No
  separate private location.
