#!/usr/bin/env python3
"""Phase-0 triage seeder for the distributed test-fix harness (plan Slice 2).

Seeds queue.db from the owner's open test issues, canonicalizes duplicates on
(test_target, test_case), and classifies each: triage_class, skip_class,
gpu_topology (a confidence-tagged HINT, never a hard gate -- ADR 0008),
priority, and a coarse Phase-0 cluster_hint. Then selects 3 dry-run canaries
(one per routing lane / category, with deterministic fallback).

Live source: `meta testinfra.issue list --owner-is=<o> --state-is=OPEN -o json`.
Offline: pass --input <file.json> (used by tests) with the same shape.

Usage:
    triage.py seed <db> [--owner mitra_training] [--input issues.json]
                        [--manifest dry_run_manifest.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

import migrate

CATEGORIES = {"FAILURE", "FLAKY", "SKIPPING"}
GPU_TOPOLOGIES = {"cpu_mockable", "single_gpu", "multi_gpu_nccl", "unknown"}
ROUTES = {"cpu", "local", "re_fallback", "re_multi"}

_MULTI = re.compile(r"nccl|world_size|distributed|multi[_-]?gpu|torchrun|fsdp|device_mesh", re.I)
_SINGLE = re.compile(r"_gpu\b|\bgpu\b|fbgemm|cuda", re.I)
_SKIP_GPU = re.compile(r"requires?\s*(cuda|gpu|nccl)|_gpu\b|nccl|fbgemm|cuda", re.I)
_SKIP_FLAG = re.compile(r"feature|flag|\bgk\b|\bjk\b|justknob|disabled", re.I)
_SKIP_DEPR = re.compile(r"deprecat", re.I)
_CATEGORY_WEIGHT = {"FAILURE": 1.0, "FLAKY": 0.6, "SKIPPING": 0.3}


def parse_test_name(name: str) -> tuple[str, str]:
    """Split an issue's test_name into (buck target, test case).

    'fbcode//a/b:rule - case (Class)'  -> ('fbcode//a/b:rule', 'case (Class)')
    'fbcode//a/b:main'                 -> ('fbcode//a/b:main', '')
    'cogwheel:foo#main'                -> ('cogwheel:foo#main', '')
    """
    if " - " in name:
        target, case = name.split(" - ", 1)
        return target.strip(), case.strip()
    return name.strip(), ""


def classify_gpu_topology(text: str) -> tuple[str, float]:
    # Pyre/type-check + lint targets are CPU regardless of "distributed" in the
    # name; check this BEFORE the GPU regexes to avoid false positives.
    if re.search(r"type[-_]check|-typecheck|:lint|-lint\b", text, re.I):
        return "cpu_mockable", 0.7
    if _MULTI.search(text):
        return "multi_gpu_nccl", 0.6
    if _SINGLE.search(text):
        return "single_gpu", 0.6
    return "cpu_mockable", 0.3  # low-confidence default; worker re-validates (ADR 0008)


def classify_skip(text: str) -> str:
    if _SKIP_GPU.search(text):
        return "gpu_unavailable"
    if _SKIP_FLAG.search(text):
        return "feature_flag_off"
    if _SKIP_DEPR.search(text):
        return "deprecated"
    return "unknown"


def normalize_target_pkg(target: str) -> str:
    """Package path without the :rule and any #suffix -- the coarse cluster key."""
    return re.sub(r"#.*$", "", target).split(":", 1)[0]


def cluster_hint(target: str) -> str:
    return hashlib.sha1(normalize_target_pkg(target).encode()).hexdigest()[:16]


def validate_unit(gpu_topology: str, route: str | None) -> None:
    """Enqueue-time consistency guard. Rejects impossible topology/route combos."""
    if gpu_topology not in GPU_TOPOLOGIES:
        raise ValueError(f"bad gpu_topology: {gpu_topology!r}")
    if route is None:
        return
    if route not in ROUTES:
        raise ValueError(f"bad route: {route!r}")
    if gpu_topology == "multi_gpu_nccl" and route != "re_multi":
        raise ValueError("multi_gpu_nccl must route to re_multi, not " + route)
    if gpu_topology == "cpu_mockable" and route not in ("cpu",):
        raise ValueError("cpu_mockable must route to cpu, not " + route)


def fetch_issues(owner: str, input_path: str | None) -> list[dict]:
    if input_path:
        with open(input_path) as fh:
            return json.load(fh)
    out = subprocess.run(
        ["meta", "testinfra.issue", "list", "--owner-is", owner,
         "--state-is", "OPEN", "-l", "500", "-o", "json"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def build_records(issues: list[dict]) -> list[dict]:
    recs = []
    for it in issues:
        name = it.get("test_name", "")
        target, case = parse_test_name(name)
        topo, conf = classify_gpu_topology(name)
        category = it.get("issue_type", "")
        rec = {
            "issue_id": str(it.get("issue_id")),
            "target": target,
            "test_case": case,
            "test_name": name,
            "category": category,
            "gpu_topology": topo,
            "gpu_topology_confidence": conf,
            "skip_class": classify_skip(name) if category == "SKIPPING" else None,
            "cluster_hint": cluster_hint(target),
            "triage_class": "actionable",  # history-based refinement (noise/likely_fixed) is a follow-up
        }
        validate_unit(rec["gpu_topology"], None)
        recs.append(rec)
    # priority second pass: category weight + normalized cluster size
    sizes: dict[str, int] = {}
    for r in recs:
        sizes[r["cluster_hint"]] = sizes.get(r["cluster_hint"], 0) + 1
    max_size = max(sizes.values()) if sizes else 1
    for r in recs:
        cw = _CATEGORY_WEIGHT.get(r["category"], 0.3)
        csize_norm = sizes[r["cluster_hint"]] / max_size
        r["priority"] = round(30 * cw + 40 * csize_norm, 4)
    return recs


def seed(db_path: str, owner: str, input_path: str | None, manifest_path: str) -> dict:
    issues = fetch_issues(owner, input_path)
    recs = build_records(issues)

    migrate.apply_migrations(db_path)
    conn = migrate.connect(db_path)
    try:
        # test_issue: one row per source issue (issue_id PK).
        for r in recs:
            conn.execute(
                "INSERT OR REPLACE INTO test_issue"
                "(issue_id, target, test_name, category, skip_class, gpu_topology,"
                " gpu_topology_confidence, cluster_hint, priority, triage_class)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (r["issue_id"], r["target"], r["test_case"], r["category"],
                 r["skip_class"], r["gpu_topology"], r["gpu_topology_confidence"],
                 r["cluster_hint"], r["priority"], r["triage_class"]),
            )
        # tests: canonical (test_target, test_case), collapsing duplicate issues.
        canon: dict[tuple[str, str], dict] = {}
        for r in recs:
            key = (r["target"], r["test_case"])
            entry = canon.setdefault(key, {"issue_ids": [], "category": r["category"]})
            if r["issue_id"] not in entry["issue_ids"]:
                entry["issue_ids"].append(r["issue_id"])
        for (target, case), entry in canon.items():
            conn.execute(
                "INSERT OR REPLACE INTO tests(test_target, test_case, issue_ids, category, status)"
                " VALUES (?,?,?,?,?)",
                (target, case, json.dumps(entry["issue_ids"]), entry["category"], "pending"),
            )
        # Enqueue one Phase-A unit per canonical test (idempotent: only when empty).
        gmap = {"cpu_mockable": "cpu", "unknown": "cpu",
                "single_gpu": "single", "multi_gpu_nccl": "multi"}
        by_issue = {r["issue_id"]: r for r in recs}
        units_enqueued = 0
        if conn.execute("SELECT COUNT(*) FROM units").fetchone()[0] == 0:
            for (target, case), entry in canon.items():
                rep = by_issue.get(entry["issue_ids"][0], {})
                gpu_req = gmap.get(rep.get("gpu_topology", "cpu_mockable"), "cpu")
                conn.execute(
                    "INSERT INTO units(test_issue_id, phase, state, gpu_req, attempts, "
                    "available_at, priority, tried_worker_ids) VALUES (?,?,?,?,?,?,?,?)",
                    (entry["issue_ids"][0], "A", "queued", gpu_req, 0, 0,
                     rep.get("priority", 0), "[]"),
                )
                units_enqueued += 1
        conn.commit()
        counts = {
            "issues": len(recs),
            "canonical_tests": len(canon),
            "duplicates_collapsed": len(recs) - len(canon),
            "units_enqueued": units_enqueued,
        }
    finally:
        conn.close()

    manifest = select_canaries(recs)
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True)
    counts["canaries"] = manifest
    return counts


def _first(recs, category, topo):
    cands = [r for r in recs if r["category"] == category and r["gpu_topology"] == topo]
    return sorted(cands, key=lambda r: r["target"])[0] if cands else None


def select_canaries(recs: list[dict]) -> dict:
    """Deterministic: one per (category, topology) lane, with fallback that
    relaxes category first, then topology, recording any substitution."""
    lanes = [
        ("cpu", "FAILURE", "cpu_mockable"),
        ("single_gpu", "SKIPPING", "single_gpu"),
        ("multi_gpu", "FLAKY", "multi_gpu_nccl"),
    ]
    out = {}
    for lane, category, topo in lanes:
        pick = _first(recs, category, topo)
        substitution = None
        if pick is None:  # relax category, keep topology
            cands = sorted([r for r in recs if r["gpu_topology"] == topo], key=lambda r: r["target"])
            if cands:
                pick = cands[0]
                substitution = "relaxed_category"
        if pick is None:  # relax topology too
            cands = sorted(recs, key=lambda r: r["target"])
            if cands:
                pick = cands[0]
                substitution = "relaxed_category_and_topology"
        out[lane] = None if pick is None else {
            "issue_id": pick["issue_id"], "target": pick["target"],
            "test_case": pick["test_case"], "category": pick["category"],
            "gpu_topology": pick["gpu_topology"], "substitution": substitution,
        }
    return out


def main() -> int:
    p = argparse.ArgumentParser(prog="triage.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("db")
    s.add_argument("--owner", default="mitra_training")
    s.add_argument("--input", default=None)
    s.add_argument("--manifest", default="dry_run_manifest.json")
    args = p.parse_args()

    if args.cmd == "seed":
        counts = seed(args.db, args.owner, args.input, args.manifest)
        json.dump(counts, sys.stdout, indent=1)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
