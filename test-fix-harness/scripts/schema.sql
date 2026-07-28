-- test-fix-harness queue.db schema (SQLite, WAL). See design spec + ADR 0001.
-- Master-owned, local disk, mode 0600. Two writers (daemon + master-Claude CLI)
-- serialized by WAL + busy_timeout + BEGIN IMMEDIATE. Additive-only migrations.

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version    INTEGER PRIMARY KEY,
  applied_at INTEGER NOT NULL,
  description TEXT
);

-- Phase-0 triage output (one row per source issue, pre-canonicalization).
CREATE TABLE IF NOT EXISTS test_issue (
  issue_id        TEXT PRIMARY KEY,
  target          TEXT,
  test_name       TEXT,
  source_path     TEXT,
  category        TEXT CHECK(category IN ('FAILURE','FLAKY','SKIPPING')),
  last_run_ts     INTEGER, last_pass_ts INTEGER, last_status TEXT,
  pass_rate       REAL, run_count_window INTEGER,
  data_freshness  TEXT CHECK(data_freshness IN ('fresh','stale')),
  needs_refresh   INTEGER DEFAULT 0,
  triage_class    TEXT,   -- actionable|noise|external_infra|likely_fixed|intentional_skip
  skip_class      TEXT,   -- gpu_unavailable|target_disabled|feature_flag_off|broken_import|deprecated|unknown
  gpu_topology    TEXT,   -- cpu_mockable|single_gpu|multi_gpu_nccl|unknown
  gpu_topology_confidence REAL,
  cluster_hint    TEXT,
  priority        REAL,
  est_gpu_seconds REAL
);

-- Canonical tests (dedup key); collapses duplicate issues.
CREATE TABLE IF NOT EXISTS tests (
  id          INTEGER PRIMARY KEY,
  test_target TEXT NOT NULL,
  test_case   TEXT NOT NULL,
  issue_ids   TEXT NOT NULL,          -- JSON array
  category    TEXT,
  status      TEXT,                    -- pending|leased|parked|done|failed|needs_human
  repro_runs  INTEGER DEFAULT 0,
  repro_total INTEGER DEFAULT 0,
  repro_rate  REAL GENERATED ALWAYS AS (CAST(repro_runs AS REAL)/NULLIF(repro_total,0)) VIRTUAL,
  root_cause_id INTEGER REFERENCES root_causes(id),
  UNIQUE(test_target, test_case)
);

-- Schedulable work units.
CREATE TABLE IF NOT EXISTS units (
  id                   INTEGER PRIMARY KEY,
  test_issue_id        TEXT,
  phase                TEXT CHECK(phase IN ('A','B')),
  root_cause_cluster_id INTEGER,
  state                TEXT CHECK(state IN ('queued','leased','parked','dispatched','done','failed','needs_human')),
  gpu_req              TEXT CHECK(gpu_req IN ('cpu','single','multi')),
  gpu_count            INTEGER DEFAULT 0,
  route                TEXT,           -- cpu|local|re_fallback|re_multi
  worker_host          TEXT,
  attempts             INTEGER DEFAULT 0,
  available_at         REAL DEFAULT 0,
  lease_expires_at     REAL,
  cost_estimate        REAL,
  park_category        TEXT, park_reason TEXT, parked_at REAL, parked_by TEXT,
  priority             REAL,
  tried_worker_ids     TEXT DEFAULT '[]'
);

-- Append-only attempt audit.
CREATE TABLE IF NOT EXISTS runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  test_id       INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
  phase         TEXT NOT NULL,
  worker_id     TEXT NOT NULL, host TEXT, route TEXT,
  attempt       INTEGER NOT NULL,
  started_at    INTEGER, ended_at INTEGER, heartbeat_at INTEGER,
  outcome       TEXT,   -- success|error|timeout|parked|reclaimed
  reproduced    INTEGER,
  termination_reason TEXT, -- completed|test_failed|test_passed|infra_preempted|infra_timeout|worker_crashed|lease_expired|oom
  result_ref    TEXT, error_summary TEXT,
  result_json   TEXT    -- verbatim testx-debug output; spill to file above 256 KiB
);

CREATE TABLE IF NOT EXISTS root_causes (
  id            INTEGER PRIMARY KEY,
  signature     TEXT NOT NULL UNIQUE,
  title         TEXT NOT NULL,
  cause_category TEXT,
  fixer_test_id INTEGER REFERENCES tests(id),
  merge_confidence REAL, review_flag TEXT,
  fix_state     TEXT,   -- pending|in_progress|diff_published|landed
  diff_url      TEXT
);
CREATE TABLE IF NOT EXISTS root_cause_tests (
  root_cause_id INTEGER NOT NULL REFERENCES root_causes(id) ON DELETE CASCADE,
  test_id       INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
  PRIMARY KEY (root_cause_id, test_id)
);

CREATE TABLE IF NOT EXISTS clusters (
  cluster_id       INTEGER PRIMARY KEY,
  cause_summary    TEXT, canonical_issue_id TEXT,
  member_count     INTEGER, evidence_ref TEXT, fix_state TEXT, created_at INTEGER
);

CREATE TABLE IF NOT EXISTS workers (
  worker_id TEXT PRIMARY KEY, host TEXT, role TEXT, capabilities TEXT,
  gpu_count INTEGER DEFAULT 0,
  state TEXT,   -- idle|busy|down|draining
  current_unit INTEGER, last_heartbeat REAL, manifest_sha TEXT
);

CREATE TABLE IF NOT EXISTS leases (
  lease_id       TEXT PRIMARY KEY,
  resource_class TEXT CHECK(resource_class IN ('cpu','local_gpu','re_single_gpu','re_multi_gpu')),
  holder_unit_id INTEGER, worker_id TEXT,
  acquired_at REAL, heartbeat_at REAL, ttl_seconds INTEGER, expires_at REAL
);

CREATE TABLE IF NOT EXISTS daemon_meta (key TEXT PRIMARY KEY, value TEXT);

CREATE INDEX IF NOT EXISTS idx_tests_dispatch ON tests(status);
CREATE INDEX IF NOT EXISTS idx_units_dispatch ON units(state, available_at, priority);
CREATE INDEX IF NOT EXISTS idx_runs_test ON runs(test_id);
CREATE INDEX IF NOT EXISTS idx_units_route_state ON units(route, state);
