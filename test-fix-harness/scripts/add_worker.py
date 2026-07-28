#!/usr/bin/env python3
"""Add a worker by hostname: SSH reachability check, GPU auto-detect, provision
what's missing, validate, then launch a dedicated 1-slot serve_fleet loop (buck2
serializes per host). Run from your LOGIN SHELL on the master (needs SSH).
Coordinates via the shared queue.db, so it does not disturb a running fleet.

Usage: python3 add_worker.py <host> [gpu_count_override]
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
TFH = os.path.expanduser("~/.tfh")
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new"]
FBCODE = "/data/users/anshulverma/fbsource/fbcode"


def _ssh(host, cmd, timeout=90):
    return subprocess.run(["ssh", *SSH_OPTS, host, cmd], capture_output=True, text=True, timeout=timeout)


def _scp(src, dst, timeout=120):
    subprocess.run(["scp", *SSH_OPTS, src, dst], check=True, capture_output=True, timeout=timeout)


def _fail(msg):
    print(f"[add_worker] ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


def add_one(host: str, gpus_override=None) -> bool:
    """Provision + validate + launch one host. Returns True on success."""
    tag = host.split(".")[0]
    print(f"\n[add_worker] === {host} ===")

    # 0. reachability
    if _ssh(host, "true", timeout=30).returncode != 0:
        print(f"[add_worker] {host}: cannot SSH (unreachable / no credential); skipping", file=sys.stderr)
        return False

    # 1. detect host kind
    if gpus_override is not None:
        gpus = gpus_override
    else:
        out = _ssh(host, 'command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L 2>/dev/null | grep -c "^GPU" || echo 0').stdout
        gpus = int("".join(ch for ch in out if ch.isdigit()) or "0")
    caps, lane = ("cpu,gpu", "any") if gpus > 0 else ("cpu", "cpu")
    print(f"[add_worker] {host} -> caps={caps} gpu_count={gpus} lane={lane}")

    # 2. provision (idempotent)
    print("[add_worker] provisioning ...")
    _ssh(host, "mkdir -p ~/.tfh/scripts ~/.tfh/templates ~/.tfh/guard")
    for f in ("run_unit.sh", "land_guard.sh"):
        _scp(os.path.join(HERE, f), f"{host}:~/.tfh/scripts/")
    for f in ("phase_A.md", "phase_B.md"):
        _scp(os.path.join(PLUGIN, "templates", f), f"{host}:~/.tfh/templates/")
    _ssh(host, "ls ~/.claude/skills/testx-debug >/dev/null 2>&1 || agent-market skill testx-debug install || true; "
               "for t in sl jf arc hg; do cp ~/.tfh/scripts/land_guard.sh ~/.tfh/guard/$t; chmod +x ~/.tfh/guard/$t; done")

    # 3. validate
    print("[add_worker] validating ...")
    ok = True
    if _ssh(host, "command -v claude").returncode != 0:
        print("  FAIL: claude not installed"); ok = False
    if _ssh(host, "test -f ~/.tfh/scripts/run_unit.sh").returncode != 0:
        print("  FAIL: run_unit.sh missing"); ok = False
    if _ssh(host, "test -f ~/.tfh/templates/phase_A.md").returncode != 0:
        print("  FAIL: phase_A.md missing"); ok = False
    if _ssh(host, "ls ~/.claude/skills/testx-debug >/dev/null 2>&1").returncode != 0:
        print("  WARN: testx-debug skill not detected (diagnosis quality may drop)")
    smoke = _ssh(host, 'claude -p "reply with the token READY and nothing else" </dev/null 2>/dev/null', timeout=120)
    print("  ok: claude headless auth works" if "READY" in smoke.stdout
          else "  WARN: claude -p smoke did not return READY (auth/first-run SSO?)")
    if not ok:
        print(f"[add_worker] {host}: validation failed; not bringing it online", file=sys.stderr)
        return False

    # 4. register + launch a dedicated 1-slot loop (idempotent: skip if already up)
    already = subprocess.run(["pgrep", "-f", f"serve_fleet.py .*hosts_{tag}.txt"],
                             capture_output=True, text=True).stdout.split()
    if already:
        print(f"[add_worker] {host}: serve loop already running (pid {already[0]}); "
              f"not launching a duplicate")
        return True
    os.makedirs(os.path.join(TFH, "logs"), exist_ok=True)
    hf = os.path.join(TFH, f"hosts_{tag}.txt")
    with open(hf, "w") as fh:
        fh.write(f'{host},worker,"{caps}",{gpus},1,{lane}\n')
    log = open(os.path.join(TFH, "logs", f"serve_{tag}.log"), "a")
    p = subprocess.Popen(["python3", os.path.join(HERE, "serve_fleet.py"),
                          os.path.join(TFH, "queue.db"), hf],
                         cwd=FBCODE, stdout=log, stderr=log,
                         env=dict(os.environ, TFH_SKIP_PREFLIGHT="1"), start_new_session=True)
    print(f"[add_worker] {host} online (1 slot) pid={p.pid}")
    return True


def main() -> int:
    # accept multiple hosts: space-separated args and/or comma-separated lists
    hosts = [h.strip() for a in sys.argv[1:] for h in a.split(",") if h.strip()]
    if not hosts:
        _fail("usage: add_worker.py <host>[,<host>...] [<host> ...]")
    online = sum(1 for h in hosts if add_one(h))
    print(f"\n[add_worker] {online}/{len(hosts)} worker(s) online")
    print(f"[add_worker] check: python3 {HERE}/dispatcher.py --db {TFH}/queue.db progress")
    return 0 if online else 1


if __name__ == "__main__":
    raise SystemExit(main())
