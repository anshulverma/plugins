#!/usr/bin/env python3
"""GPU/RE lease scheduler for the distributed test-fix harness (plan Slice 6).

Capacity is PARTITIONED, not a shared pool (ADR 0004): re_multi_gpu (multi/NCCL
only), re_single_gpu (single-GPU overflow only), local_gpu. A single-GPU flood
can never take a multi permit, so multi never starves. Single-GPU tries local
first and only falls back to RE after local_wait_timeout_s. Leases live in the
`leases` table (durable) so a reaper can reclaim a crashed worker's permit and
no slot leaks. Per-unit and daily RE GPU-minute budgets cap runaway usage.
"""
from __future__ import annotations

import sqlite3

DEFAULT_CFG = {
    "local_gpu_slots": 1,
    "re_single_gpu_fallback_slots": 1,
    "re_multi_gpu_slots": 2,
    "local_wait_timeout_s": 90,
    "gpu_ttl_s": 1800,
    "re_gpu_minutes_per_unit": 30,
    "re_gpu_minutes_per_day": 600,
}

# route -> resource_class
_ROUTE_RC = {"local": "local_gpu", "re_fallback": "re_single_gpu", "re_multi": "re_multi_gpu"}
_RC_SLOTS = {
    "local_gpu": "local_gpu_slots",
    "re_single_gpu": "re_single_gpu_fallback_slots",
    "re_multi_gpu": "re_multi_gpu_slots",
}


def used(conn: sqlite3.Connection, resource_class: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM leases WHERE resource_class=?", (resource_class,)
    ).fetchone()[0]


def _grant(conn, unit_id, worker_id, resource_class, route, now, cfg):
    lease_id = f"{unit_id}:{resource_class}:{int(now)}"
    conn.execute(
        "INSERT INTO leases(lease_id, resource_class, holder_unit_id, worker_id, "
        "acquired_at, heartbeat_at, ttl_seconds, expires_at) VALUES (?,?,?,?,?,?,?,?)",
        (lease_id, resource_class, unit_id, worker_id, now, now,
         cfg["gpu_ttl_s"], now + cfg["gpu_ttl_s"]),
    )
    conn.commit()
    return route


def acquire(conn, gpu_req, unit_id, worker_id, now, cfg=DEFAULT_CFG, waited_s=0):
    """Just-in-time permit acquisition. Returns the granted route, or None when
    no permit is available (caller waits or parks). CPU needs no permit."""
    if gpu_req == "cpu":
        return "cpu"
    if gpu_req == "single":
        if used(conn, "local_gpu") < cfg["local_gpu_slots"]:
            return _grant(conn, unit_id, worker_id, "local_gpu", "local", now, cfg)
        if waited_s >= cfg["local_wait_timeout_s"] and \
                used(conn, "re_single_gpu") < cfg["re_single_gpu_fallback_slots"]:
            return _grant(conn, unit_id, worker_id, "re_single_gpu", "re_fallback", now, cfg)
        return None  # local busy and (still waiting OR RE fallback full)
    if gpu_req == "multi":
        if used(conn, "re_multi_gpu") < cfg["re_multi_gpu_slots"]:
            return _grant(conn, unit_id, worker_id, "re_multi_gpu", "re_multi", now, cfg)
        return None  # scarce multi pool full -> park
    raise ValueError(f"bad gpu_req {gpu_req!r}")


def release(conn, unit_id) -> int:
    n = conn.execute("DELETE FROM leases WHERE holder_unit_id=?", (unit_id,)).rowcount
    conn.commit()
    return n


def reap(conn, now) -> int:
    """Reclaim permits from expired leases (crashed worker / TTL). Returns count
    of permits freed so no slot ever leaks (ADR 0004)."""
    n = conn.execute("DELETE FROM leases WHERE expires_at < ?", (now,)).rowcount
    conn.commit()
    return n


def unit_budget_ok(spent_minutes: float, cfg=DEFAULT_CFG) -> bool:
    return spent_minutes < cfg["re_gpu_minutes_per_unit"]


def daily_budget_ok(spent_today_minutes: float, cfg=DEFAULT_CFG) -> bool:
    return spent_today_minutes < cfg["re_gpu_minutes_per_day"]
