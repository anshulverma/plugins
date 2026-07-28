# Hermes federation — spec (FUTURE EXTENSION)

Status: **future extension — NOT built now.** Date: 2026-07-28.
Parent: `hermes/docs/DESIGN.md`.

Hermes ships **flat**: one root Hermes owns a `queue.db`, musters a `crew` of
hosts, and dispatches `tickets`. This document specs an optional **federation**
layer for later, so today's design can adopt a few cheap seams (§14) that keep the
door open without paying any distributed-systems cost now.

The rule for adopting this: **don't build it until a concrete trigger appears**
(§2). When one does, this is the design.

---

## 1. What federation is

A **tree of Hermes nodes**. A parent Hermes delegates a **shard** (a batch of
tickets) to a **deputy** Hermes, which runs it against its own `crew` and streams
results/status back up. A deputy is itself a full Hermes, so it can have its own
deputies — **arbitrary depth** (root → deputy → deputy → … → crew). Nothing about
a node changes with depth; only its position does.

```
                 hermes (root)
                /            \
        deputy hermes      deputy hermes        ← each owns a crew shard
         /       \               |
   deputy      deputy          crew             ← deputies can have deputies
     |            |
   crew         crew
```

A deputy is the recursion of the same engine: **"a lieutenant is just a Hermes"**
— same queue/dispatch/lease/health/reduce machinery, one level up.

## 2. When to reach for it (triggers)

Build federation only when the flat model actually strains:

- **Scale beyond one root**: SSH fan-out, one SQLite writer, one box's CPU for
  reduce/verify, or the concurrency cap becomes the bottleneck.
- **Geography / network domains**: crew spans regions, datacenters, or security
  zones. A deputy local to each zone dispatches with low latency, keeps SSH
  in-zone, and survives cross-zone partitions.
- **Organizational boundaries**: different teams own different crews; a deputy per
  team/owner.

If none of these hold (e.g. a few hundred tickets from one devserver + a handful
of hosts), the flat model is correct and federation is over-engineering.

## 3. Terminology

- **Root Hermes** — the top node; the one a human starts a run on.
- **Deputy Hermes** — a child node a parent delegates a shard to; itself a full
  Hermes (recursive; a deputy may have its own deputies).
- **Shard** — the batch of tickets a parent assigns to one deputy.
- **crew**, **ticket**, **playbook**, **reduction** — unchanged from the flat
  design.

## 4. Delegation link (decided: reuse the control-plane API)

A parent talks to a deputy over the **sub-project-3 control-plane HTTP API** — the
same JSON + websocket API the web UI uses. **The parent is an API client of each
deputy.** No separate inter-node protocol is invented.

- **Push**: parent submits a shard via the deputy's "submit ticket batch into a
  run" endpoint (the same endpoint the UI uses to inject externally-created
  tickets).
- **Pull**: parent consumes the deputy's `events since(cursor)` stream (websocket
  or polling) to mirror status/findings upward; and reads the deputy's
  `HealthReport` for liveness.
- **Auth**: the bearer-token model (loopback-bind default; token at
  `$HERMES_HOME/api_token`) extends to **parent↔deputy**: the deputy is bound to
  its zone-reachable interface (not loopback), and the parent holds the deputy's
  token. Cross-node auth inherits the same rotation semantics (`--rotate-token`
  invalidates in-flight parent sessions, which reconnect). Non-loopback binding
  carries the same trusted-network/proxy caveat already noted in DESIGN §10.
- Deputies are **provisioned like crew** (SSH bootstrap: install Hermes, start its
  API server, health-gate on admission), then driven over the API.

## 5. Work assignment (sharding & routing)

A deputy advertises its **capabilities** (resource classes, zone) exactly as a
crew member advertises resources. The parent routes a ticket to a deputy whose
capabilities satisfy the ticket's `resource_req` (+ zone affinity), balancing
load — i.e. the flat `claim`/routing logic applied one level up. The parent keeps
a **delegation ledger** (§12) recording which tickets went to which deputy, plus
that deputy's event cursor, so it can track and reclaim.

## 6. Reduce model (decided: global roll-up + optional pre-reduce)

- **Default — global roll-up**: deputies stream **raw findings** upward; the
  **root runs the single authoritative `reduce`** across all findings. Safe with
  no assumption about the playbook.
- **Opt-in — associative pre-reduce**: a playbook whose `reduce` is
  associative/composable may set `reduce_associative = True`; deputies then
  pre-reduce their shard locally and stream **reductions** (not raw findings)
  upward, and the root reduces over those. Cuts upstream data and parallelizes the
  reduce, but is only correct when `reduce` is associative — the engine trusts the
  playbook's flag and documents the contract.
- `verify` (master re-verify) runs at the node that owns the crew member that
  produced the result (the deputy), and again is re-checkable at the root; the
  no-trust invariant holds at every level.

## 7. Lease / resource model (decided: local pools + opt-in global semaphore)

- **Default — local disjoint pools**: each deputy owns its own resource pool
  (capacity = Σ its crew's `resources_json[class]`, exactly as flat). Correct when
  resources are physically partitioned per deputy/zone. No cross-node lease
  coordination — no round-trips, no shared state.
- **Opt-in — parent-held global semaphore**: a genuinely shared scarce pool (e.g.
  one global RE/GPU quota) is modeled as a **parent-held semaphore** that deputies
  request grants from over the API before dispatching against that class. Only the
  classes declared "global" pay the round-trip; everything else stays local. A
  grant is a lease (TTL 1800s, renewed on the parent heartbeat, reclaimed on
  deputy loss) — the flat lease mechanics, delegated.

## 8. Failure & reassignment

- The parent probes each deputy's health on its heartbeat sweep (over the API). A
  deputy that fails goes `down` in the parent's node registry.
- Because **a deputy owns its own `queue.db`**, a transient outage is recoverable:
  the parent prefers **resume** if the deputy returns within a grace window
  (`HERMES_DEPUTY_GRACE_S`, default 300s); past the window it **reassigns** the
  shard's non-terminal tickets to other capable deputies (or reclaims them
  locally).
- Reassignment is safe by the flat invariants: nothing auto-ships, tickets are
  idempotent (a re-dispatched ticket is just re-diagnosed/re-fixed), and the
  delegation ledger (§12) records exactly which tickets to reclaim. A deputy that
  later revives finds its shard already reclaimed and drops it (fenced by a
  delegation epoch/token to avoid double-work).

## 9. Status & events roll-up

The parent mirrors each deputy's `events since(cursor)` into its own `events`
table, tagged with the deputy's node id (and a node path for depth). `hermes
status` and the SPA render a **tree**: per-node crew/ticket/lease rollups with
drill-down into any subtree. Attention conditions (parked ratio, all-crew-down,
no-progress) roll up: a node is "in attention" if it or any descendant is.

## 10. Safety: no-ship transitivity

The no-ship invariant is **per-Hermes**, so it holds at **every level
automatically**: every leaf worker runs under its owning node's site guard
(`guarantees_no_ship` + `guard_installed` health gate), regardless of how deep the
tree is. A deputy enforces no-ship on its crew exactly as the root does; a parent
never needs to trust a deputy's guard — it re-verifies (`verify`) results it rolls
up, same as the flat no-trust rule.

## 11. Multi-level specifics & depth cap

Depth is unbounded by construction (recursion of one protocol), but bounded in
practice by a configurable **`HERMES_MAX_DEPTH`** (default 3) validated at
delegation time to prevent runaway trees. Federation is primarily about
**breadth** (many deputies) — depth is for genuine hierarchy of zones (region →
zone → rack), not for its own sake.

## 12. Data-model additions (deferred — added only when federation is built)

Additive, per-node (no shared DB; the flat invariant is preserved):

- `nodes` — registered deputies: id, parent-relative endpoint URL, token ref,
  capabilities, zone, state (idle/busy/down/draining), health_json,
  last_heartbeat, event_cursor.
- `delegations` — the ledger: ticket_id, deputy_node_id, epoch, state
  (delegated/acked/reclaimed), assigned_at.
- `tickets` gains an optional `origin` marker distinguishing locally-seeded from
  parent-delegated tickets (so a deputy knows which to report vs own).

The root and every deputy keep their **own** `queue.db` under their own
`HERMES_HOME` — federation never introduces a shared database or peer-to-peer
state, so the flat design's core invariant survives.

## 13. Interaction with the recursion (driver-as-Hermes) note

Distinct from crew-sharding federation, a *single ticket* can also become a
sub-run when its `driver` runs `hermes run` locally (a worker that decomposes its
task into its own run). That "recursion" flavor and this "federation" flavor
compose but are separate: federation shards an existing run's tickets across
deputies; recursion turns one ticket into a nested run. Both rely on the same
seam (§14): a Hermes node is reachable/usable exactly like the engine itself.

## 14. Federation-ready seams to adopt NOW (cheap; keep the door open)

These cost a note today and avoid a rewrite later. Adopt them in the flat build:

1. **Shape the control-plane API (sub-project 3) as the north-bound delegation
   API too.** Ensure it includes (a) "submit a batch of externally-created tickets
   into a run" and (b) "`events since(cursor)`" — both already needed by the UI —
   so a future parent can be a plain API client. Recorded in DESIGN §10.
2. **Keep `driver.command` opaque enough that `hermes run` can be a driver.**
   Already true in the flat `Driver`/`/goal` model; just reserve that a ticket's
   result may summarize a nested run.
3. **Per-node `HERMES_HOME` / own `queue.db`, no shared state.** Already the flat
   invariant — do not add any cross-process shared DB, so federation stays additive.
4. **Make `verify` and the no-ship guard strictly per-node** (already the case) so
   transitivity is automatic.

Nothing else in the flat engine changes for federation.

## 15. Explicitly NOT built now / open questions

- **Not built now**: `nodes`/`delegations` tables, deputy provisioning, the
  parent-as-API-client loop, global-semaphore grants, tree status roll-up. All
  deferred until a §2 trigger.
- Open when built: cross-node clock skew handling for heartbeats/leases; back-
  pressure when a deputy's crew is saturated (park at parent vs. queue at deputy);
  whether the SPA renders one federated tree or per-node views with a switcher;
  exact fencing token/epoch scheme for reassignment idempotency.
