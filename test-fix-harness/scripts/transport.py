#!/usr/bin/env python3
"""Transports + capability-aware routing + serve loop (plan Slice 5/6 wiring).

Two transports move a claimed unit to a worker:
- local_transport: runs run_unit.sh on THIS host (the master, devgpu004 with 8x
  H100). No SSH -> usable from the agent sandbox for the "start now" path.
- ssh_transport(host): scp payload + ssh run_unit.sh on an OD worker. Used by the
  daemon launched in the user's login shell (the agent sandbox cannot SSH).

Routing (2026-07-23 update): GPU host serves cpu/single/multi (single-node
multi-GPU/NCCL runs LOCALLY on the 8 H100s); cpu hosts serve cpu only. RE is not
used here (reserved for true multi-node; RE-adding fixes are flagged, not auto).
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess

import contracts
import dispatcher

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
TFH_DEFAULT = os.path.expanduser("~/.tfh")


# ---------- routing ----------
def allowed_gpu_reqs(capabilities: str) -> set[str]:
    caps = {c.strip() for c in capabilities.replace('"', "").split(",")}
    return {"cpu", "single", "multi"} if "gpu" in caps else {"cpu"}


def route_for(gpu_req: str) -> str:
    return {"cpu": "cpu", "single": "local", "multi": "local_multi"}.get(gpu_req, "cpu")


# ---------- payload plumbing ----------
def build_envelope(conn, unit, base_commit: str, timeout_s: int) -> dict:
    ti = None
    if unit.get("test_issue_id"):
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute("SELECT * FROM test_issue WHERE issue_id=?",
                           (unit["test_issue_id"],)).fetchone()
        ti = dict(row) if row else None
    env = {
        "unit_id": str(unit["id"]),
        "phase": unit.get("phase") or "A",
        "test_target": (ti or {}).get("target", ""),
        "test_case": (ti or {}).get("test_name", ""),
        "gpu_req": unit.get("gpu_req") or "cpu",
        "route": unit.get("route") or route_for(unit.get("gpu_req") or "cpu"),
        "base_commit": base_commit,
        "payload_sha256": "",  # filled after write
        "category": (ti or {}).get("category"),
        # per-lane wall-clock cap enforced by run_unit.sh's `timeout` wrapper.
        # Phase B (esp. de-flake, which reruns the test >=50x) needs a much larger
        # cap than a Phase-A diagnosis on the same lane.
        "timeout_s": (
            {"cpu": 3600, "single": 5400, "multi": 7200}
            if unit.get("phase") == "B"
            else {"cpu": 1200, "single": 1800, "multi": 3600}
        ).get(unit.get("gpu_req") or "cpu", timeout_s),
        "cluster_id": None,
        "prior_attempts": [],
        "root_cause_summary": None,
        "culprit_symbol": None,
        "deflake": None,
    }
    # Phase B: attach the root cause the fixer must address. The fixer test's
    # target/case already come from test_issue above; the summary + culprit drive
    # the fix, and `deflake` routes flaky causes through the de-flake protocol.
    rcid = unit.get("root_cause_cluster_id")
    if (env["phase"] == "B") and rcid:
        conn.row_factory = __import__("sqlite3").Row
        rc = conn.execute("SELECT title, cause_category FROM root_causes WHERE id=?",
                          (rcid,)).fetchone()
        if rc:
            env["cluster_id"] = str(rcid)
            env["root_cause_summary"] = rc["title"]
            env["culprit_symbol"] = rc["title"]
            cat = (rc["cause_category"] or "").lower().replace("-", "_")
            env["deflake"] = ("flaky" in cat) or (env.get("category") == "FLAKY")
    return env


def _write_payload(tfh: str, unit_id, env: dict) -> tuple[str, str]:
    d = os.path.join(tfh, "units", str(unit_id))
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "payload.json")
    with open(path, "w") as fh:
        json.dump(env, fh)
    sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
    return path, sha


def _read_result(tfh: str, unit_id) -> dict | None:
    p = os.path.join(tfh, "units", str(unit_id), "result.json")
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p))
    except (json.JSONDecodeError, OSError):
        return None


# ---------- transports ----------
def local_transport(env: dict, unit: dict, tfh: str = TFH_DEFAULT):
    """Run the unit on this host via run_unit.sh (no SSH)."""
    env = dict(env)
    _, sha = _write_payload(tfh, unit["id"], env)
    # rewrite payload with the sha embedded is unnecessary; run_unit re-hashes file
    timeout = (env.get("timeout_s") or 1800) + 120
    r = subprocess.run(
        ["bash", os.path.join(SCRIPTS, "run_unit.sh"), str(unit["id"]), sha],
        env={**os.environ, "TFH_DIR": tfh, "TFH_SKIP_PREFLIGHT": os.environ.get("TFH_SKIP_PREFLIGHT", "0")},
        capture_output=True, text=True, timeout=timeout,
    )
    return _read_result(tfh, unit["id"]), r.returncode


def ssh_transport(host: str, tfh_remote: str = "~/.tfh", tfh_local: str = TFH_DEFAULT):
    """Return a transport that runs the unit on `host` over SSH (daemon-only;
    the agent sandbox has no SSH credential). scp payload -> ssh run_unit.sh."""
    ssh_opts = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4",
                "-o", "StrictHostKeyChecking=accept-new"]

    def _t(env: dict, unit: dict):
        uid = str(unit["id"])
        path, sha = _write_payload(tfh_local, uid, dict(env))
        remote_dir = f"{tfh_remote}/units/{uid}"
        subprocess.run(["ssh", *ssh_opts, host, f"mkdir -p {remote_dir}"], check=True, timeout=90)
        subprocess.run(["scp", *ssh_opts, path, f"{host}:{remote_dir}/payload.json"],
                       check=True, timeout=120)
        timeout = (env.get("timeout_s") or 1800) + 180
        r = subprocess.run(
            ["ssh", *ssh_opts, host,
             f"TFH_DIR={tfh_remote} TFH_SKIP_PREFLIGHT=1 bash {tfh_remote}/scripts/run_unit.sh "
             f"{shlex.quote(uid)} {shlex.quote(sha)}"],
            capture_output=True, text=True, timeout=timeout,
        )
        # pull the result back, plus the evidence file (best-effort) so evidence
        # from remote workers is durable on the master, not lost.
        subprocess.run(["scp", *ssh_opts, f"{host}:{remote_dir}/result.json",
                        os.path.join(tfh_local, "units", uid, "result.json")],
                       capture_output=True, timeout=60)
        subprocess.run(["scp", *ssh_opts, f"{host}:{remote_dir}/evidence.txt",
                        os.path.join(tfh_local, "units", uid, "evidence.txt")],
                       capture_output=True, timeout=60)
        return _read_result(tfh_local, uid), r.returncode

    return _t


# ---------- serve ----------
def serve_once_for_host(conn, host: str, capabilities: str, transport, base_commit: str,
                        timeout_s: int, tfh: str = TFH_DEFAULT, now=None, gpu_reqs=None):
    """Claim one unit this host may run, dispatch it, record the result.
    `gpu_reqs` overrides the host's capability-derived set (e.g. to make the
    master take GPU-only units while ODs take CPU). Returns the unit id, or None."""
    allowed = gpu_reqs if gpu_reqs is not None else allowed_gpu_reqs(capabilities)
    unit = dispatcher.claim_unit(conn, host, now=now, gpu_reqs=allowed)
    if unit is None:
        return None
    _now = dispatcher._now() if now is None else now
    # Envelope/validation errors are our-side problems -> real attempt penalty.
    try:
        env = build_envelope(conn, unit, base_commit, timeout_s)
        contracts.validate_named(env, "dispatch")
    except Exception as e:
        print(f"[{host}] unit {unit['id']} envelope error: {e}", flush=True)
        dispatcher.requeue(conn, unit, _now, reason="envelope_error")
        return unit["id"]
    # Transport (SSH/scp) errors mean the unit NEVER RAN -> no attempt penalty,
    # reset affinity so any worker (incl. the reliable master) can retry it.
    try:
        result, code = transport(env, unit)
    except Exception as e:
        print(f"[{host}] unit {unit['id']} transport(ssh) error: {e}", flush=True)
        dispatcher.requeue_transport(conn, unit, _now)
        return unit["id"]
    dispatcher.record_result(conn, unit["id"], host, result, code, now=now)
    return unit["id"]
