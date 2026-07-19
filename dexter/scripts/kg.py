#!/usr/bin/env python3
"""Append-only knowledge graph for a perf-investigation.

The graph is the source of truth for accumulated knowledge. Every mutation is a
new line appended to knowledge-graph/graph.jsonl -- nothing is ever rewritten, so
the full history (including status changes) is preserved. `render` reproduces the
current state from the log.

Node types : question | fact | hypothesis | experiment | observation
Edge rels  : supports | refutes | motivates | depends_on | tests | answers
Verdicts   : a hypothesis becomes confirmed|refuted only via `verdict`, which
             should cite >=2 independent job ids (multi-test validation).

Usage:
  kg.py <INV_ROOT> node --id H1 --type hypothesis --text "..." [--status open]
  kg.py <INV_ROOT> edge --src O3 --dst H1 --rel refutes
  kg.py <INV_ROOT> verdict --id H1 --status refuted --confidence 0.99 --evidence job1,job2 --note "..."
  kg.py <INV_ROOT> render        # writes graph.dot + STATUS.md
  kg.py <INV_ROOT> show          # prints current status to stdout
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

VALID_NODE_TYPES = {"question", "fact", "hypothesis", "experiment", "observation"}
VALID_RELS = {"supports", "refutes", "motivates", "depends_on", "tests", "answers"}
VALID_STATUS = {"open", "confirmed", "refuted", "blocked"}


def _graph_path(root: str) -> str:
    return os.path.join(root, "knowledge-graph", "graph.jsonl")


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _append(root: str, obj: dict) -> None:
    obj["ts"] = _now()
    with open(_graph_path(root), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _load(root: str) -> list[dict]:
    path = _graph_path(root)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _current_state(root: str) -> tuple[dict, list[dict]]:
    """Fold the append-only log into current nodes (by id) and edges."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    for rec in _load(root):
        op = rec.get("op")
        if op == "node":
            nodes[rec["id"]] = {
                "id": rec["id"],
                "type": rec["type"],
                "text": rec.get("text", ""),
                "status": rec.get("status", "open"),
                "confidence": None,
                "evidence": [],
                "note": "",
            }
        elif op == "verdict" and rec["id"] in nodes:
            n = nodes[rec["id"]]
            n["status"] = rec["status"]
            n["confidence"] = rec.get("confidence")
            n["evidence"] = rec.get("evidence", [])
            n["note"] = rec.get("note", "")
        elif op == "edge":
            edges.append(rec)
    return nodes, edges


def cmd_node(root, a):
    if a.type not in VALID_NODE_TYPES:
        sys.exit(f"invalid --type {a.type}; one of {sorted(VALID_NODE_TYPES)}")
    status = a.status or "open"
    if status not in VALID_STATUS:
        sys.exit(f"invalid --status {status}")
    _append(root, {"op": "node", "id": a.id, "type": a.type, "text": a.text, "status": status})
    print(f"node {a.id} ({a.type}) added")


def cmd_edge(root, a):
    if a.rel not in VALID_RELS:
        sys.exit(f"invalid --rel {a.rel}; one of {sorted(VALID_RELS)}")
    _append(root, {"op": "edge", "src": a.src, "dst": a.dst, "rel": a.rel})
    print(f"edge {a.src} -{a.rel}-> {a.dst} added")


def cmd_verdict(root, a):
    if a.status not in VALID_STATUS:
        sys.exit(f"invalid --status {a.status}")
    evidence = [e for e in (a.evidence or "").split(",") if e]
    if a.status in {"confirmed", "refuted"} and len(evidence) < 2:
        print(
            "WARNING: multi-test rule wants >=2 independent job ids in --evidence "
            f"(got {len(evidence)}). Recording anyway; add more before trusting it.",
            file=sys.stderr,
        )
    _append(root, {
        "op": "verdict", "id": a.id, "status": a.status,
        "confidence": a.confidence, "evidence": evidence, "note": a.note or "",
    })
    print(f"verdict {a.id} -> {a.status} (evidence: {evidence})")


def _render(root) -> str:
    nodes, edges = _current_state(root)
    lines = ["# Knowledge Graph Status", ""]
    for st in ("hypothesis",):
        pass
    by_type: dict[str, list[dict]] = {}
    for n in nodes.values():
        by_type.setdefault(n["type"], []).append(n)
    # hypotheses first with verdicts
    hyps = by_type.get("hypothesis", [])
    lines.append("## Hypotheses")
    if not hyps:
        lines.append("_none yet_")
    for h in hyps:
        conf = f" (conf {h['confidence']})" if h["confidence"] is not None else ""
        ev = f" [evidence: {', '.join(h['evidence'])}]" if h["evidence"] else ""
        lines.append(f"- **{h['id']}** `{h['status']}`{conf}: {h['text']}{ev}")
        if h["note"]:
            lines.append(f"    - note: {h['note']}")
    for t in ("question", "fact", "experiment", "observation"):
        items = by_type.get(t, [])
        if not items:
            continue
        lines.append("")
        lines.append(f"## {t.capitalize()}s")
        for n in items:
            lines.append(f"- **{n['id']}**: {n['text']}")
    lines.append("")
    lines.append("## Edges")
    for e in edges:
        lines.append(f"- {e['src']} —{e['rel']}→ {e['dst']}")
    return "\n".join(lines) + "\n"


def _render_dot(root) -> str:
    nodes, edges = _current_state(root)
    color = {"open": "gray", "confirmed": "green", "refuted": "red", "blocked": "orange"}
    shape = {
        "question": "diamond", "hypothesis": "box", "fact": "note",
        "experiment": "ellipse", "observation": "oval",
    }
    out = ["digraph knowledge {", "  rankdir=LR;", '  node [style=filled,fillcolor=white];']
    for n in nodes.values():
        c = color.get(n["status"], "gray") if n["type"] == "hypothesis" else "white"
        s = shape.get(n["type"], "box")
        label = (n["text"][:48] + "…") if len(n["text"]) > 49 else n["text"]
        out.append(f'  "{n["id"]}" [shape={s},fillcolor={c},label="{n["id"]}: {label}"];')
    for e in edges:
        out.append(f'  "{e["src"]}" -> "{e["dst"]}" [label="{e["rel"]}"];')
    out.append("}")
    return "\n".join(out) + "\n"


def cmd_render(root, a):
    with open(os.path.join(root, "knowledge-graph", "STATUS.md"), "w", encoding="utf-8") as fh:
        fh.write(_render(root))
    with open(os.path.join(root, "knowledge-graph", "graph.dot"), "w", encoding="utf-8") as fh:
        fh.write(_render_dot(root))
    print("rendered STATUS.md + graph.dot")


def cmd_show(root, a):
    sys.stdout.write(_render(root))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root")
    sub = p.add_subparsers(dest="cmd", required=True)
    pn = sub.add_parser("node"); pn.add_argument("--id", required=True); pn.add_argument("--type", required=True); pn.add_argument("--text", required=True); pn.add_argument("--status")
    pe = sub.add_parser("edge"); pe.add_argument("--src", required=True); pe.add_argument("--dst", required=True); pe.add_argument("--rel", required=True)
    pv = sub.add_parser("verdict"); pv.add_argument("--id", required=True); pv.add_argument("--status", required=True); pv.add_argument("--confidence", type=float); pv.add_argument("--evidence"); pv.add_argument("--note")
    sub.add_parser("render")
    sub.add_parser("show")
    a = p.parse_args()
    {"node": cmd_node, "edge": cmd_edge, "verdict": cmd_verdict, "render": cmd_render, "show": cmd_show}[a.cmd](a.root, a)


if __name__ == "__main__":
    main()
