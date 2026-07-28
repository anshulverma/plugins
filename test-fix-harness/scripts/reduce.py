#!/usr/bin/env python3
"""Reduce / dedup step for the distributed test-fix harness (plan Slice 7).

Stage 1 (here, deterministic + blocking): merge Phase-A reports by cause
fingerprint = signature (sha1(normalize(culprit_symbol)+'|'+cause_category)).
A test with multiple root causes joins multiple clusters (many-to-many). Reports
with neither a culprit_symbol nor an error_signature become insufficient-evidence
singletons that never anchor a cause cluster. Non-reproduced reports are never
excluded; they cluster on their hypothesized cause.

Stage 2 (LLM semantic adjudication by master Claude) is an injectable hook
`adjudicator(clusters)->clusters`; omitted here (deterministic stage only).
Output is a deterministic JSON + Markdown pair for human review before Phase B.
"""
from __future__ import annotations

import json
import os
import sqlite3


def reduce(entries: list[dict], adjudicator=None) -> list[dict]:
    """entries: [{"test_id": int, "report": <phase_a dict>}]. Returns clusters
    sorted deterministically (size desc, then signature)."""
    clusters: dict[str, dict] = {}

    def _add(sig, tid, category, summary, flag):
        c = clusters.setdefault(sig, {
            "signature": sig, "cause_category": category,
            "cause_summary": summary, "test_ids": [], "review_flag": flag,
        })
        if tid not in c["test_ids"]:
            c["test_ids"].append(tid)

    for e in entries:
        tid = e["test_id"]
        rep = e["report"]
        rcs = rep.get("root_causes") or []
        if not rcs:
            _add(f"insufficient:test{tid}", tid, None,
                 "no root cause reported", "insufficient-evidence")
            continue
        for rc in rcs:
            culprit = rc.get("culprit_symbol") or ""
            errsig = rc.get("error_signature") or ""
            if not culprit and not errsig:
                _add(f"insufficient:test{tid}", tid, rc.get("cause_category"),
                     "no culprit/error signature", "insufficient-evidence")
                continue
            sig = rc.get("signature") or f"sig:{culprit}|{rc.get('cause_category')}"
            summary = rc.get("mechanism") or culprit or errsig
            _add(sig, tid, rc.get("cause_category"), summary, None)

    out = sorted(clusters.values(), key=lambda c: (-len(c["test_ids"]), c["signature"]))
    for c in out:
        c["size"] = len(c["test_ids"])
    if adjudicator is not None:
        out = adjudicator(out)
    return out


def persist(conn: sqlite3.Connection, clusters: list[dict]) -> None:
    """Write clusters into root_causes + root_cause_tests (real FK junction)."""
    for c in clusters:
        conn.execute(
            "INSERT OR IGNORE INTO root_causes(signature, title, cause_category, "
            "fix_state, review_flag) VALUES (?,?,?,?,?)",
            (c["signature"], c["cause_summary"] or c["signature"],
             c["cause_category"], "pending", c["review_flag"]),
        )
        rc_id = conn.execute(
            "SELECT id FROM root_causes WHERE signature=?", (c["signature"],)
        ).fetchone()[0]
        for tid in c["test_ids"]:
            conn.execute(
                "INSERT OR IGNORE INTO root_cause_tests(root_cause_id, test_id) VALUES (?,?)",
                (rc_id, tid),
            )
    conn.commit()


def write_report(clusters: list[dict], runid: str, out_dir: str) -> tuple[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    jpath = os.path.join(out_dir, f"clusters-{runid}.json")
    mpath = os.path.join(out_dir, f"clusters-{runid}.md")
    with open(jpath, "w") as fh:
        json.dump(clusters, fh, indent=1, sort_keys=True)
    lines = [f"# Reduce report {runid}", "", f"{len(clusters)} clusters", ""]
    for c in clusters:
        lines.append(f"## {c['signature']} (size {c['size']}"
                     + (f", {c['review_flag']}" if c["review_flag"] else "") + ")")
        lines.append(f"- cause: {c['cause_summary']}")
        lines.append(f"- tests: {c['test_ids']}")
        lines.append("")
    with open(mpath, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return jpath, mpath
