#!/usr/bin/env python3
"""Control-plane daemon core for the distributed test-fix harness (plan Slice 5).

Owns the queue.db dispatch state machine: CAS claim with worker anti-affinity,
in-flight leases, a reaper for expired leases, requeue-with-backoff (attempts
increment on INFRA failure only, ADR 0006), park state machine, and crash
recovery (reconcile orphaned in-flight units on startup).

The transport (how a claimed unit reaches a worker) is injectable so the whole
core is testable via a local L0 mock-worker with NO SSH. Production `serve()`
wraps these sync DB ops with an asyncio fan-out over real `ssh` transport
(ADR 0002); the DB stays single-writer.

Unit state machine: queued -> dispatched -> done | parked | failed_permanent,
with dispatched -> queued on requeue.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time

import contracts
import migrate

DEFAULTS = {
    "max_attempts": 3,
    "lease_ttl_s": 1800,
    "backoff_cap_s": 300,
}


def connect(db_path: str) -> sqlite3.Connection:
    """Dispatcher connection in autocommit mode so explicit BEGIN IMMEDIATE
    CAS transactions behave (pysqlite's implicit txn management would otherwise
    conflict with an explicit BEGIN)."""
    conn = migrate.connect(db_path)
    conn.isolation_level = None
    return conn


def _now() -> float:
    return time.time()


def _backoff(attempts: int, cap: int) -> float:
    return min(cap, 30 * (2 ** max(0, attempts - 1)))


def _get_unit(conn: sqlite3.Connection, unit_id) -> dict | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM units WHERE id=?", (unit_id,)).fetchone()
    return dict(row) if row else None


def claim_unit(conn, worker_id, now=None, cfg=DEFAULTS, gpu_reqs=None):
    """Atomically claim the highest-priority eligible unit not yet tried by this
    worker. `gpu_reqs` (a set) restricts to units this host can run. Returns the
    claimed unit dict, or None."""
    now = _now() if now is None else now
    conn.execute("BEGIN IMMEDIATE")
    try:
        if gpu_reqs:
            ph = ",".join("?" for _ in gpu_reqs)
            rows = conn.execute(
                f"SELECT id, tried_worker_ids FROM units "
                f"WHERE state='queued' AND available_at<=? AND gpu_req IN ({ph}) "
                f"ORDER BY priority DESC, id ASC",
                (now, *sorted(gpu_reqs)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, tried_worker_ids FROM units "
                "WHERE state='queued' AND available_at<=? "
                "ORDER BY priority DESC, id ASC",
                (now,),
            ).fetchall()
        pick = None
        for uid, tried in rows:
            tw = json.loads(tried or "[]")
            if worker_id not in tw:
                pick = (uid, tw)
                break
        if pick is None and rows:
            # Fallback: every eligible unit was already tried by THIS worker.
            # With a single-host fleet strict anti-affinity would deadlock a unit
            # that failed once, so allow a re-claim here. max_attempts still bounds
            # total retries (record_result parks a persistently-failing unit), and
            # tried_worker_ids is not re-appended, so a freshly-added worker is
            # still preferred on the next round.
            uid, tried = rows[0]
            pick = (uid, json.loads(tried or "[]"))
        if pick is None:
            conn.execute("COMMIT")
            return None
        uid, tw = pick
        if worker_id not in tw:
            tw.append(worker_id)
        conn.execute(
            "UPDATE units SET state='dispatched', worker_host=?, "
            "lease_expires_at=?, tried_worker_ids=? WHERE id=?",
            (worker_id, now + cfg["lease_ttl_s"], json.dumps(tw), uid),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return _get_unit(conn, uid)


def _log_run(conn, unit, worker_id, outcome, now, termination=None):
    tid = conn.execute(
        "SELECT id FROM tests WHERE test_target=? LIMIT 1",
        (unit.get("test_issue_id") or "",),
    ).fetchone()
    # test linkage is best-effort in the core; runs.test_id may be null in mock.
    test_id = tid[0] if tid else None
    if test_id is not None:
        conn.execute(
            "INSERT INTO runs(test_id, phase, worker_id, host, attempt, ended_at, outcome, termination_reason) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (test_id, unit.get("phase", "A"), worker_id, worker_id,
             unit.get("attempts", 0), int(now), outcome, termination),
        )


def requeue(conn, unit, now, reason, cfg=DEFAULTS):
    """Infra failure path: increment attempts, backoff, park when exhausted."""
    attempts = int(unit["attempts"]) + 1
    if attempts >= cfg["max_attempts"]:
        conn.execute(
            "UPDATE units SET state='parked', attempts=?, park_category=?, "
            "park_reason=?, parked_at=?, lease_expires_at=NULL WHERE id=?",
            (attempts, "infra_exhausted", reason, now, unit["id"]),
        )
        conn.commit()
        return "parked"
    conn.execute(
        "UPDATE units SET state='queued', attempts=?, available_at=?, "
        "worker_host=NULL, lease_expires_at=NULL WHERE id=?",
        (attempts, now + _backoff(attempts, cfg["backoff_cap_s"]), unit["id"]),
    )
    conn.commit()
    return "requeued"


def requeue_transport(conn, unit, now, backoff=30):
    """Transport/SSH dispatch failed - the unit never actually ran on a worker.
    Requeue WITHOUT an attempt penalty and reset worker affinity, since SSH
    flakiness is transient and must not permanently park an undiagnosed unit."""
    conn.execute(
        "UPDATE units SET state='queued', available_at=?, worker_host=NULL, "
        "lease_expires_at=NULL, tried_worker_ids='[]' WHERE id=?",
        (now + backoff, unit["id"]),
    )
    conn.commit()
    return "requeued_transport"


def record_result(conn, unit_id, worker_id, result, exit_code, now=None, cfg=DEFAULTS):
    """Apply a worker outcome. exit_code 0 + valid result => terminal (done);
    non-zero (infra) => requeue/park. A completed diagnosis (even reproduced=false)
    is terminal and never retried (ADR 0006)."""
    now = _now() if now is None else now
    unit = _get_unit(conn, unit_id)
    if unit is None:
        raise ValueError(f"unknown unit {unit_id}")

    if exit_code != 0 or result is None:
        return requeue(conn, unit, now, reason=f"exit_{exit_code}", cfg=cfg)

    # Validate the worker's report against the contract for its phase.
    schema = "phase_a" if unit.get("phase", "A") == "A" else "phase_b"
    contracts.validate_named(result, schema)

    status = result.get("status")
    if status in ("NEEDS_GPU_EXEC", "NEEDS_REROUTE"):
        # Not terminal, not a failure: requeue immediately (no attempt penalty)
        # for the GPU-exec/reroute lane (Slice 6 refines routing).
        conn.execute(
            "UPDATE units SET state='queued', available_at=?, worker_host=NULL, "
            "lease_expires_at=NULL WHERE id=?",
            (now, unit["id"]),
        )
        conn.commit()
        return "rerouted"

    _log_run(conn, unit, worker_id, "success", now, termination="completed")
    conn.execute(
        "UPDATE units SET state='done', worker_host=NULL, lease_expires_at=NULL WHERE id=?",
        (unit["id"],),
    )
    # Phase B: record the published diff on the root cause so the funnel/report
    # reflect fix progress. DIFF_LOCAL_ONLY means submit failed but a patch exists.
    if unit.get("phase") == "B" and unit.get("root_cause_cluster_id"):
        fix_state = "diff_published" if status == "completed" else "diff_local_only"
        conn.execute(
            "UPDATE root_causes SET diff_url=?, fix_state=? WHERE id=?",
            (result.get("diff_url"), fix_state, unit["root_cause_cluster_id"]),
        )
    conn.commit()
    return "done"


def reap_leases(conn, now=None, cfg=DEFAULTS):
    """Requeue/park units whose in-flight lease has expired (dead worker/timeout)."""
    now = _now() if now is None else now
    conn.row_factory = sqlite3.Row
    expired = [dict(r) for r in conn.execute(
        "SELECT * FROM units WHERE state='dispatched' AND lease_expires_at IS NOT NULL "
        "AND lease_expires_at < ?", (now,)).fetchall()]
    for unit in expired:
        requeue(conn, unit, now, reason="lease_expired", cfg=cfg)
    return len(expired)


def recover(conn, now=None):
    """Startup crash recovery: orphaned in-flight units (child ssh died with the
    daemon) go back to queued without an attempt penalty."""
    now = _now() if now is None else now
    n = conn.execute(
        "UPDATE units SET state='queued', worker_host=NULL, lease_expires_at=NULL "
        "WHERE state='dispatched'"
    ).rowcount
    conn.commit()
    return n


def dispatch_once(conn, worker_id, transport, now=None, cfg=DEFAULTS):
    """Claim one unit, send it via `transport(envelope, unit)->(result, exit)`,
    and record the outcome. Returns the unit id handled, or None if idle."""
    now = _now() if now is None else now
    unit = claim_unit(conn, worker_id, now, cfg)
    if unit is None:
        return None
    envelope = {
        "unit_id": str(unit["id"]),
        "phase": unit.get("phase") or "A",
        "test_target": unit.get("test_issue_id") or "",
        "test_case": "",
        "gpu_req": unit.get("gpu_req") or "cpu",
        "base_commit": "PINNED",
        "payload_sha256": "0" * 64,
        "route": unit.get("route"),
    }
    contracts.validate_named(envelope, "dispatch")
    result, exit_code = transport(envelope, unit)
    record_result(conn, unit["id"], worker_id, result, exit_code, now, cfg)
    return unit["id"]


def status_counts(conn) -> dict:
    return dict(conn.execute("SELECT state, COUNT(*) FROM units GROUP BY state").fetchall())


def status_report(conn) -> dict:
    """Pull-based dashboard payload (plan Slice 9): counts by state, parked by
    category, and in-flight by route."""
    return {
        "by_state": dict(conn.execute("SELECT state, COUNT(*) FROM units GROUP BY state")),
        "parked_by_category": dict(conn.execute(
            "SELECT COALESCE(park_category,'?'), COUNT(*) FROM units "
            "WHERE state='parked' GROUP BY park_category")),
        "dispatched_by_route": dict(conn.execute(
            "SELECT COALESCE(route,'none'), COUNT(*) FROM units "
            "WHERE state='dispatched' GROUP BY route")),
        "attention": attention(conn),
    }


def show(conn, ident) -> dict:
    """Accepts either a unit id (matches units/<id> dirs) or a test issue id."""
    conn.row_factory = sqlite3.Row
    ti = None
    units = []
    if str(ident).isdigit():
        u = conn.execute("SELECT * FROM units WHERE id=?", (ident,)).fetchone()
        if u:
            units = [dict(u)]
            iid = dict(u).get("test_issue_id")
            if iid:
                ti = conn.execute("SELECT * FROM test_issue WHERE issue_id=?", (iid,)).fetchone()
    if ti is None and not units:  # fall back to interpreting it as an issue id
        ti = conn.execute("SELECT * FROM test_issue WHERE issue_id=?", (str(ident),)).fetchone()
        units = [dict(r) for r in conn.execute(
            "SELECT * FROM units WHERE test_issue_id=?", (str(ident),))]
    tid = dict(ti)["issue_id"] if ti else None
    runs = []
    if tid:
        t = conn.execute("SELECT id FROM tests WHERE issue_ids LIKE ?", (f'%"{tid}"%',)).fetchone()
        if t:
            runs = [dict(r) for r in conn.execute(
                "SELECT phase, worker_id, outcome, termination_reason, ended_at "
                "FROM runs WHERE test_id=? ORDER BY id", (t[0],))]
    # inline the result.json if the worker produced one
    import os as _os, json as _json
    for u in units:
        rp = _os.path.expanduser(f"~/.tfh/units/{u['id']}/result.json")
        if _os.path.exists(rp):
            try:
                u["result"] = _json.load(open(rp))
            except (OSError, ValueError):
                u["result"] = None
    return {"test_issue": dict(ti) if ti else None, "units": units, "runs": runs}


def health(conn) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM units").fetchone()[0]
    done = conn.execute("SELECT COUNT(*) FROM units WHERE state='done'").fetchone()[0]
    inflight = conn.execute("SELECT COUNT(*) FROM units WHERE state='dispatched'").fetchone()[0]
    return {"units_total": total, "units_done": done, "in_flight": inflight}


def attention(conn, parked_ratio_threshold=0.5) -> list[str]:
    """Threshold banners surfaced in status (no paging, ADR: pull-based)."""
    total = conn.execute("SELECT COUNT(*) FROM units").fetchone()[0] or 0
    banners = []
    if total:
        parked = conn.execute("SELECT COUNT(*) FROM units WHERE state='parked'").fetchone()[0]
        if parked / total > parked_ratio_threshold:
            banners.append(f"parked_ratio {parked}/{total} exceeds {parked_ratio_threshold}")
    w_total = conn.execute("SELECT COUNT(*) FROM workers").fetchone()[0]
    if w_total:
        w_down = conn.execute("SELECT COUNT(*) FROM workers WHERE state='down'").fetchone()[0]
        if w_down == w_total:
            banners.append("all workers down")
    gpu_parked = conn.execute(
        "SELECT COUNT(*) FROM units WHERE state='parked' AND park_category LIKE '%gpu%'"
    ).fetchone()[0]
    if gpu_parked:
        banners.append(f"{gpu_parked} units parked on GPU capacity")
    return banners


def _fmt_dur(secs) -> str:
    if secs is None:
        return "?"
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m{secs % 60:02d}s"
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"


def progress(conn, tfh=None, lease_ttl=1800, now=None) -> str:
    """Human-readable live dashboard: in-flight units per worker with elapsed
    time, per-unit turnaround stats, throughput, and an ETA. Timing comes from
    the payload.json/result.json mtimes in each unit dir (no schema/worker
    changes needed)."""
    import os
    import statistics
    import time as _time
    tfh = tfh or os.path.expanduser("~/.tfh")
    now = now or _time.time()
    conn.row_factory = sqlite3.Row

    def mt(uid, name):
        p = os.path.join(tfh, "units", str(uid), name)
        try:
            return os.path.getmtime(p)
        except OSError:
            return None

    rows = [dict(r) for r in conn.execute(
        "SELECT u.id, u.state, u.worker_host, u.gpu_req, u.lease_expires_at, "
        "ti.target, ti.test_name FROM units u LEFT JOIN test_issue ti ON u.test_issue_id=ti.issue_id")]

    import socket
    me = socket.gethostname().split(".")[0]

    def live_state(r):
        """True=live, False=stale/dead, None=unknown. LOCAL tests use the
        heartbeat file (authoritative here); REMOTE tests use lease expiry, since
        the worker's heartbeat is written on the remote box and never synced back
        (a leftover master-side heartbeat file must NOT be trusted for remotes)."""
        wh = r["worker_host"] or ""
        if wh.startswith(me) or wh.endswith("-local"):
            try:
                return (now - os.path.getmtime(os.path.join(tfh, "units", str(r["id"]), "heartbeat"))) < 180
            except OSError:
                return None
        le = r["lease_expires_at"]
        return None if le is None else (le > now)

    states, inflight, durations, recent = {}, [], [], 0
    for r in rows:
        states[r["state"]] = states.get(r["state"], 0) + 1
        if r["state"] == "dispatched":
            dt = mt(r["id"], "payload.json")
            if dt is None and r["lease_expires_at"]:
                dt = r["lease_expires_at"] - lease_ttl  # fallback for older claims
            inflight.append((r, (now - dt) if dt else None, live_state(r)))
        elif r["state"] == "done":
            pt, rt = mt(r["id"], "payload.json"), mt(r["id"], "result.json")
            if pt and rt and rt >= pt:
                durations.append(rt - pt)
            if rt and rt > now - 600:
                recent += 1

    done = states.get("done", 0)
    queued = states.get("queued", 0)
    parked = states.get("parked", 0)
    remaining = queued + len(inflight)
    stale = sum(1 for _, _, hb in inflight if hb is False)
    # active = distinct hosts with a live (or unknown-but-not-stale) in-flight unit
    active = len({r["worker_host"] for r, _, hb in inflight if r["worker_host"] and hb is not False})
    avg = statistics.mean(durations) if durations else None
    med = statistics.median(durations) if durations else None
    eta = (remaining * avg / active) if (avg and remaining and active > 0) else None

    lines = []
    lines.append(f"queued={queued}  in-flight={len(inflight)}  done={done}  parked={parked}"
                 f"  (total {sum(states.values())})")
    lines.append("")
    lines.append(f"{'WORKER':<34} {'TEST':>5} {'LANE':>6} {'ELAPSED':>8}  TARGET :: CASE")
    for r, el, hb in sorted(inflight, key=lambda x: (x[0]['worker_host'] or '')):
        tgt = (r["target"] or "").split(":")[-1] or (r["target"] or "")
        case = (r["test_name"] or "").split(" (")[0]  # drop the trailing (Class) qualifier
        label = (f"{tgt} :: {case}" if case else tgt)[:70]
        mark = "  [STALE]" if hb is False else ""
        lines.append(f"{(r['worker_host'] or '?'):<34} {r['id']:>5} {r['gpu_req']:>6} "
                     f"{_fmt_dur(el):>8}  {label}{mark}")
    lines.append("")
    lines.append(f"turnaround/unit: avg {_fmt_dur(avg)}  median {_fmt_dur(med)}  "
                 f"(n={len(durations)})")
    wline = f"throughput: {recent} done in last 10m  |  active workers: {active}"
    if stale:
        wline += f"  ({stale} stale/orphaned in-flight)"
    lines.append(wline)
    if active == 0:
        if inflight:
            lines.append("ETA: n/a - in-flight tests are stale/orphaned; no live worker. "
                         "Run `dispatcher.py recover` and (re)start serve_fleet.")
        else:
            lines.append("ETA: n/a - NO ACTIVE WORKERS (nothing in flight). Start serve_fleet.")
    elif eta:
        fin = now + eta
        lines.append(f"ETA: ~{_fmt_dur(eta)} for {remaining} remaining  ->  finish ~"
                     + _time.strftime('%H:%M:%S', _time.localtime(fin)))
    else:
        lines.append("ETA: n/a (need at least one completed unit with timing)")
    return "\n".join(lines)


def run_sql(conn, query: str):
    """Read-only escape hatch: only SELECT/PRAGMA allowed."""
    q = query.strip().lower()
    if not (q.startswith("select") or q.startswith("pragma")):
        raise ValueError("run_sql allows only SELECT/PRAGMA")
    return conn.execute(query).fetchall()


def main() -> int:
    p = argparse.ArgumentParser(prog="dispatcher.py")
    p.add_argument("--db", default="queue.db")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("recover")
    sub.add_parser("health")
    sh = sub.add_parser("show"); sh.add_argument("issue_id")
    sq = sub.add_parser("sql"); sq.add_argument("query")
    pr = sub.add_parser("progress")
    pr.add_argument("--watch", action="store_true")
    pr.add_argument("--interval", type=int, default=5)
    args = p.parse_args()
    conn = connect(args.db)
    try:
        if args.cmd == "progress":
            if args.watch:
                import time as _t
                try:
                    while True:
                        print("\033[2J\033[H" + progress(conn), flush=True)
                        _t.sleep(args.interval)
                except KeyboardInterrupt:
                    pass
            else:
                print(progress(conn))
        elif args.cmd == "status":
            print(json.dumps(status_report(conn), indent=1))
        elif args.cmd == "recover":
            print(f"recovered={recover(conn)}")
        elif args.cmd == "health":
            print(json.dumps(health(conn), indent=1))
        elif args.cmd == "show":
            print(json.dumps(show(conn, args.issue_id), indent=1))
        elif args.cmd == "sql":
            for row in run_sql(conn, args.query):
                print(list(row))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
