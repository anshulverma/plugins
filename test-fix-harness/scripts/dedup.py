#!/usr/bin/env python3
"""Reduce/dedup step: cluster all done Phase-A diagnoses into unique root causes,
persist them (root_causes + root_cause_tests + clusters), and write + optionally
paste a human-review cluster report. Run at Phase-A completion.

Usage: dedup.py [--dry] [--paste]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import reduce as reduce_mod       # noqa: E402
from metrics import classify_cause  # noqa: E402

TFH = os.path.expanduser("~/.tfh")
DB = os.path.join(TFH, "queue.db")


def _build(conn):
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT u.id, u.test_issue_id, ti.target, ti.test_name, ti.category "
        "FROM units u JOIN test_issue ti ON u.test_issue_id=ti.issue_id "
        "WHERE u.state='done' AND u.phase='A'")]
    entries, meta = [], {}
    for r in rows:
        try:
            res = json.load(open(os.path.join(TFH, "units", str(r["id"]), "result.json")))
        except (OSError, ValueError):
            continue
        entries.append({"test_id": r["id"], "report": res})
        meta[r["id"]] = r
    return entries, meta


def _issue_to_testid(conn):
    m = {}
    for tid, issue_ids in conn.execute("SELECT id, issue_ids FROM tests"):
        try:
            for iid in json.loads(issue_ids or "[]"):
                m[str(iid)] = tid
        except ValueError:
            pass
    return m


def _member(meta, uid):
    r = meta.get(uid, {})
    tgt = (r.get("target") or "").split(":")[-1]
    case = (r.get("test_name") or "").split(" (")[0]
    return f"{tgt}::{case}" if case else tgt


def main() -> int:
    ap = argparse.ArgumentParser(prog="dedup.py")
    ap.add_argument("--dry", action="store_true", help="compute + print, do not persist/paste")
    ap.add_argument("--paste", action="store_true", help="submit the cluster report as a paste")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    conn.isolation_level = None
    entries, meta = _build(conn)
    clusters = reduce_mod.reduce(entries)
    real = [c for c in clusters if not c["signature"].startswith("insufficient")]
    for c in real:
        cats = {meta[t]["category"] for t in c["test_ids"] if t in meta}
        c["fix_class"] = classify_cause(c["cause_category"])
        c["deflake"] = ("FLAKY" in cats) or c["fix_class"] == "flaky"

    rc_total = sum(len((e["report"].get("root_causes") or [])) for e in entries)
    by_fix = {}
    for c in real:
        by_fix[c["fix_class"]] = by_fix.get(c["fix_class"], 0) + 1
    n_deflake = sum(1 for c in real if c["deflake"])

    # ---- human-review report (markdown) ----
    L = ["# Reduce / dedup - root-cause cluster report", ""]
    L.append(f"{len(entries)} diagnosed tests -> {rc_total} findings -> "
             f"**{len(real)} distinct root causes** "
             f"(collapse {round(rc_total/max(1,len(real)),2)}x)")
    L.append(f"fix-class: {by_fix}   |   de-flake clusters: {n_deflake}")
    L.append("")
    L.append("| tests | fix-class | deflake | category | cause | member tests |")
    L.append("|---|---|---|---|---|---|")
    for c in real:
        members = ", ".join(_member(meta, t) for t in c["test_ids"][:6])
        if len(c["test_ids"]) > 6:
            members += f", +{len(c['test_ids'])-6} more"
        L.append(f"| {c['size']} | {c['fix_class']} | {'yes' if c['deflake'] else ''} | "
                 f"{c['cause_category'] or '?'} | {(c['cause_summary'] or '')[:90]} | {members} |")
    report = "\n".join(L) + "\n"

    if args.dry:
        print(report[:4000])
        print(f"\n[dry] {len(real)} clusters, {n_deflake} de-flake, fix-class {by_fix}")
        return 0

    # ---- persist ----
    i2t = _issue_to_testid(conn)
    for c in clusters:
        conn.execute(
            "INSERT OR IGNORE INTO root_causes(signature, title, cause_category, fix_state, review_flag) "
            "VALUES (?,?,?,?,?)",
            (c["signature"], (c["cause_summary"] or c["signature"])[:200],
             c.get("cause_category"), "pending", c.get("review_flag")))
        rcid = conn.execute("SELECT id FROM root_causes WHERE signature=?", (c["signature"],)).fetchone()[0]
        for uid in c["test_ids"]:
            iid = meta.get(uid, {}).get("test_issue_id")
            tid = i2t.get(str(iid))
            if tid:
                conn.execute("INSERT OR IGNORE INTO root_cause_tests(root_cause_id, test_id) VALUES (?,?)",
                             (rcid, tid))
    os.makedirs(os.path.join(TFH, "reduce"), exist_ok=True)
    rp = os.path.join(TFH, "reduce", "clusters.md")
    open(rp, "w").write(report)
    open(os.path.join(TFH, "reduce", "clusters.json"), "w").write(json.dumps(real, indent=1, sort_keys=True))
    print(f"persisted {len(real)} root causes; report -> {rp}")

    if args.paste:
        r = subprocess.run(["meta", "phabricator.paste", "create",
                            "--title", "Mitra test-fix harness - root-cause cluster report",
                            "--language", "markdown", "--content", report, "--output", "json"],
                           capture_output=True, text=True)
        print("paste:", (r.stdout.strip() or r.stderr.strip())[-300:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
