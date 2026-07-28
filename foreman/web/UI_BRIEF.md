# Foreman control-plane — UI brief

A design brief for the Foreman web control plane (`foreman/web/`, a React + Vite +
TypeScript SPA over the engine's JSON API).

**How to use this:** paste the "Brief" section below into Claude Design (or any
UI-generation tool) to generate the initial screens. Whatever it produces becomes
the concrete spec for this directory; the component names listed here are the ones
the JSON API will be wired to.

**Vocabulary note:** the brief is site-agnostic, but the mock data is seeded with a
Meta-flavored example (`fbcode//…`, `devgpu042`). Strip those for a neutral demo.

---

## Brief

Design a web control plane called "Foreman" — a dashboard for orchestrating fleets
of headless Claude Code agents that run work across many remote hosts.

### Product context
Foreman is a generic engine that fans "tickets" (units of work) across a "crew"
(a pool of remote hosts, each running a headless AI agent). Different job types
are defined by "playbooks":
  - mechanic  — diagnoses and fixes large batches of failing/flaky tests
  - rigger    — iteratively optimizes a metric (e.g. model training efficiency)
  - medic     — root-causes a production incident (SEV)
A "run" is one invocation of a playbook. Runs move through phases (e.g. diagnose
-> reduce -> fix). Nothing ships automatically: agents stop at a proposed change
that a human reviews. This is an operator's cockpit — used to watch progress,
spot problems fast, and take control actions.

### Core vocabulary (use these exact terms in the UI)
- Crew / crew member = the pool of remote hosts and each individual host
- Ticket = one unit of work (has a state, a phase, a resource requirement)
- Run = one playbook invocation over a batch of tickets
- Playbook = the job-type methodology (mechanic / rigger / medic)
- Finding / Reduction = an agent's result and the deduped/aggregated conclusion
- Lease = a claim on a scarce resource (e.g. a GPU)
- Health = per-host status probe

### Screens to design
1. **Run overview (home)** — pick/summarize the active run: playbook, site, base
   revision, elapsed time, overall progress, phase timeline, ETA, and 4-6 KPI
   stat tiles (tickets total/done/in-flight/parked/failed, crew online, throughput
   /min, unique findings). Include attention banners for problems.
2. **Ticket board** — a kanban of tickets by state (queued, dispatched, running,
   reducing, done, parked, failed, needs-human) with counts per column; each card
   shows ticket id, target/subject, phase, resource req chip (cpu/gpu), assigned
   host, attempts, elapsed. Filter by state/phase/resource/host; search.
3. **Ticket drill-down** (drawer/modal) — payload sent to the agent, the strict
   result, evidence links, attempt history timeline, live log tail, and actions:
   requeue, reprioritize, park, open evidence.
4. **Crew panel** — one row/card per host: hostname, site, state
   (idle/busy/down/draining), a health summary (reachable, agent auth, workspace
   ready, guard installed, latency), resources (e.g. 8x H100), current ticket,
   throughput, last heartbeat. Actions: add host (modal with a live health-check
   progress checklist), drain, remove, re-probe health.
5. **Findings / reductions** — the deduped root-causes or metric results with the
   member tickets rolled up under each; a "propose change / published diff" state
   per finding; human review affordance.
6. **Live activity feed** — a streaming event log (ticket claimed, result recorded,
   phase advanced, host went down, lease acquired), filterable.

### Interactions (this is a control plane, not read-only)
Start/resume/stop a run; add/drain/remove a host; requeue/reprioritize/park a
ticket; acknowledge attention banners. Show optimistic UI + toasts. Everything
updates live (assume a websocket) — show subtle "live" indicators and smooth
transitions, not full-page reloads.

### States to cover
Loading skeletons, empty states (no run yet / empty column / no crew), error
states, and the "attention" states: parked ratio > 50%, all crew down, no progress
> 30 min, resource overflow. Make problems impossible to miss without being noisy.

### Aesthetic & tech
- An operator/ops-console feel: dense but calm, scannable, data-forward. Think a
  refined internal tool — clear hierarchy, generous use of status color used
  meaningfully (not decoratively).
- Full light AND dark mode, both first-class.
- Accessible: color is never the only signal (pair with icon/label), keyboard
  navigable, good contrast.
- Build as a React + TypeScript SPA (Vite). Componentized and reusable:
  StatTile, StatusPill, TicketCard, KanbanColumn, CrewRow, HealthBadge,
  EventRow, AttentionBanner, Drawer. Use a consistent design-token palette.
- Charts kept minimal and purposeful (throughput sparkline, phase timeline,
  ticket-state distribution) — clarity over decoration.

### Mock data (generate realistic examples matching these shapes)
```
run:      { id, playbook:"mechanic", site:"meta", base_ref:"a1b2c3d",
            state:"running", phase:"diagnose", started_at, tickets:{total:214,
            done:96, running:12, parked:8, failed:3, queued:95} }
ticket:   { id, run_id, phase, state, subject:"fbcode//foo:bar - testBaz",
            resource_req:"gpu", host:"devgpu042", attempts:1, elapsed_s:83,
            priority:57 }
finding:  { id, kind:"root_cause", title, category, member_ticket_ids:[...],
            fix_state:"diff_published", diff_url }
crew:     { id:"devgpu042", site:"meta", state:"busy",
            resources:{gpu:8, cpu:96}, current_ticket:"t-1207",
            health:{ reachable:true, agent_ok:true, auth_ok:true,
                     workspace_ready:true, guard_installed:true, latency_ms:41 },
            throughput_per_min:0.7, last_heartbeat:"3s ago" }
event:    { ts, kind:"result_recorded", host, ticket_id, message }
lease:    { id, resource_class:"gpu", holder_ticket:"t-1207", host, ttl_s:5400 }
```

Design the full set of screens with these mock records populated so the layouts
feel real. Prioritize the Run overview, Ticket board, and Crew panel.

---

## Components the API will target
`StatTile` · `StatusPill` · `TicketCard` · `KanbanColumn` · `CrewRow` ·
`HealthBadge` · `EventRow` · `AttentionBanner` · `Drawer`
