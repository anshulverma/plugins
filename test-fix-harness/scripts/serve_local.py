#!/usr/bin/env python3
# Read ~/.claude/LOCAL.md if present for local names and details.
"""Local serve loop for the master GPU host - the "start now" path
that needs no SSH (plan: execution-model update 2026-07-23).

Processes up to N queued units on THIS host via local_transport (real headless
claude + the debug skill + buck2). Installs a land-guard on PATH first so even with
--dangerously-skip-permissions nothing can land/push during diagnosis.

Usage: serve_local.py <db> [max_units] [gpu|cpu|any]
"""
from __future__ import annotations

import os
import shutil
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import dispatcher  # noqa: E402
import transport  # noqa: E402

TFH = os.path.expanduser("~/.tfh")


def _install_land_guard() -> str:
    guard = os.path.join(TFH, "guard")
    os.makedirs(guard, exist_ok=True)
    for tool in ("sl", "jf", "arc", "hg"):
        dst = os.path.join(guard, tool)
        shutil.copyfile(os.path.join(HERE, "land_guard.sh"), dst)
        os.chmod(dst, 0o755)
    os.environ["PATH"] = guard + os.pathsep + os.environ["PATH"]
    os.environ["TFH_LANDGUARD_LOG"] = os.path.join(TFH, "logs", "land_guard.log")
    return guard


def main() -> int:
    db = sys.argv[1]
    max_units = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    lane = sys.argv[3] if len(sys.argv) > 3 else "any"
    caps = '"cpu,gpu"'
    gpu_reqs = {"gpuonly": {"single", "multi"}, "cpu": {"cpu"}}.get(lane)  # None = any

    _install_land_guard()
    os.environ.setdefault("TFH_SKIP_PREFLIGHT", "1")  # master runs its own checkout
    base = os.environ.get("TFH_BASE", "LOCAL")

    conn = dispatcher.connect(db)

    def local(env, unit):
        return transport.local_transport(env, unit, tfh=TFH)

    done = 0
    while done < max_units:
        h = transport.serve_once_for_host(
            conn, socket.gethostname().split(".")[0] + "-local", caps, local, base, 1800, tfh=TFH, gpu_reqs=gpu_reqs)
        if h is None:
            print("no eligible units; idle", flush=True)
            break
        done += 1
        st = conn.execute("SELECT state FROM units WHERE id=?", (h,)).fetchone()[0]
        print(f"processed unit {h} -> {st} ({done}/{max_units})", flush=True)
    conn.close()
    print(f"serve_local finished: {done} units", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
