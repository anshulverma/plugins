#!/usr/bin/env python3
"""End-of-run (and mid-run) metrics + charts for the test-fix harness.

Reads queue.db + per-unit dir mtimes (payload.json=dispatch, result.json=done)
+ result.json contents + root_causes, and writes CSVs, best-effort PNG charts
(matplotlib -> ASCII fallback), and run-report.md. Safe on PARTIAL data (no
dedup / no Phase-B yet). Nothing here mutates the queue.

Usage: metrics.py <db> [--tfh ~/.tfh] [--out ~/.tfh/report]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reduce as reduce_mod  # noqa: E402  (Stage-1 fingerprint grouping)

# ---- cause taxonomy (see CAUSE_CATEGORIES.md): cause_category -> fix_class ----
TAXONOMY = {
    "product_bug": "code", "test_bug": "code", "type_error": "code",
    "missing_dependency": "infra", "build_error": "infra", "config": "infra",
    "resource_env": "infra", "data_dependent": "infra",
    "flaky_timing": "flaky", "intentional_skip": "wontfix", "unknown": "unknown",
}
FIX_CLASSES = ("code", "infra", "flaky", "wontfix", "unknown")


def classify_cause(cat: str) -> str:
    """Map a cause_category to its fix_class. Handles the fixed enum directly and
    falls back to fuzzy matching for legacy free-form values already in the data."""
    c = (cat or "").lower().replace("-", "_")
    if c in TAXONOMY:
        return TAXONOMY[c]
    if any(k in c for k in ("fbpkg", "missing", "dependency", "package", "registration")):
        return "infra"
    if any(k in c for k in ("config", "build", "env", "resource", "gpu", "data", "acl")):
        return "infra"
    if any(k in c for k in ("race", "concurren", "timing", "flaky", "timeout")):
        return "flaky"
    if any(k in c for k in ("intentional", "deprecat", "skip", "disabled", "gated")):
        return "wontfix"
    if any(k in c for k in ("type", "logic", "assert", "runtime", "error_handling",
                            "dtype", "device_mismatch", "numeric", "tolerance")):
        return "code"
    return "unknown"


def _mt(tfh, uid, name):
    p = os.path.join(tfh, "units", str(uid), name)
    try:
        return os.path.getmtime(p)
    except OSError:
        return None


def _result(tfh, uid):
    p = os.path.join(tfh, "units", str(uid), "result.json")
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return None


def collect(conn, tfh):
    conn.row_factory = sqlite3.Row
    units = [dict(r) for r in conn.execute(
        "SELECT u.*, ti.category, ti.gpu_topology FROM units u "
        "LEFT JOIN test_issue ti ON u.test_issue_id=ti.issue_id")]
    for u in units:
        u["dispatch_ts"] = _mt(tfh, u["id"], "payload.json")
        u["complete_ts"] = _mt(tfh, u["id"], "result.json") if u["state"] == "done" else None
        u["duration"] = (u["complete_ts"] - u["dispatch_ts"]) \
            if (u["complete_ts"] and u["dispatch_ts"] and u["complete_ts"] >= u["dispatch_ts"]) else None
        try:
            u["worker_done"] = json.loads(u["tried_worker_ids"] or "[]")[-1] if u["state"] == "done" else None
        except (ValueError, IndexError):
            u["worker_done"] = None
        u["result"] = _result(tfh, u["id"]) if u["state"] == "done" else None
    return units


def funnel(conn, units):
    n_issue = conn.execute("SELECT COUNT(*) FROM test_issue").fetchone()[0]
    n_canon = conn.execute("SELECT COUNT(*) FROM tests").fetchone()[0]
    n_diag = sum(1 for u in units if u["state"] == "done" and u["phase"] == "A")
    n_rc = conn.execute("SELECT COUNT(*) FROM root_causes").fetchone()[0]
    n_fix = sum(1 for u in units if u["state"] == "done" and u["phase"] == "B")
    n_diffs = conn.execute("SELECT COUNT(*) FROM root_causes WHERE diff_url IS NOT NULL").fetchone()[0]
    n_landed = conn.execute("SELECT COUNT(*) FROM root_causes WHERE fix_state='landed'").fetchone()[0]
    return [("open issues", n_issue), ("canonical tests", n_canon), ("diagnosed", n_diag),
            ("root causes", n_rc), ("fixes done", n_fix), ("diffs published", n_diffs),
            ("landed", n_landed)]


def _bars(pairs, width=40):
    mx = max((v for _, v in pairs), default=0) or 1
    return [f"  {str(k):<22} {v:>5} " + "#" * int(width * v / mx) for k, v in pairs]


def render_charts(out, series):
    """Best-effort PNGs; returns list of written files (empty if no matplotlib)."""
    written = []
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return written

    def save(fig, name):
        p = os.path.join(out, name)
        fig.savefig(p, bbox_inches="tight", dpi=110)
        plt.close(fig)
        written.append(p)

    if series["burnup"]:
        xs = [m for m, _ in series["burnup"]]
        ys = [c for _, c in series["burnup"]]
        fig, ax = plt.subplots(figsize=(7, 3.5))
        ax.plot(xs, ys, marker=".")
        ax.set_xlabel("minutes since first completion"); ax.set_ylabel("cumulative diagnosed")
        ax.set_title("Diagnosis burn-up"); ax.grid(True, alpha=.3)
        save(fig, "burnup.png")
    if series["durations"]:
        fig, ax = plt.subplots(figsize=(7, 3.5))
        ax.hist([d / 60 for d in series["durations"]], bins=20)
        ax.set_xlabel("turnaround (min)"); ax.set_ylabel("tests")
        ax.set_title("Per-test turnaround"); ax.grid(True, alpha=.3)
        save(fig, "turnaround_hist.png")
    for key, title, fname in (("workers", "Tests per worker", "workers.png"),
                              ("causes", "Root-cause classes", "causes.png"),
                              ("clusters", "Cluster sizes (tests per root cause)", "clusters.png")):
        data = series[key]
        if data:
            fig, ax = plt.subplots(figsize=(7, 3.5))
            ax.bar([str(k) for k, _ in data], [v for _, v in data])
            ax.set_title(title); plt.xticks(rotation=30, ha="right"); ax.grid(True, axis="y", alpha=.3)
            save(fig, fname)
    return written


def _svg_bar(pairs, title, path, w=680, h=320):
    if not pairs:
        return None
    pad = 45
    bw = (w - 2 * pad) / max(1, len(pairs))
    mx = max(v for _, v in pairs) or 1
    el = [f'<text x="{w/2}" y="18" font-size="13" text-anchor="middle" font-family="sans-serif">{title}</text>']
    for i, (k, v) in enumerate(pairs):
        bh = (h - 2 * pad) * v / mx
        x = pad + i * bw
        y = h - pad - bh
        cx = x + bw * 0.4
        el.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw*0.8:.1f}" height="{bh:.1f}" fill="#4c78a8"/>')
        el.append(f'<text x="{cx:.1f}" y="{y-3:.1f}" font-size="9" text-anchor="middle">{v}</text>')
        el.append(f'<text x="{cx:.1f}" y="{h-pad+10}" font-size="9" text-anchor="end" '
                  f'font-family="sans-serif" transform="rotate(-35 {cx:.1f} {h-pad+10})">{str(k)[:20]}</text>')
    open(path, "w").write(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">{"".join(el)}</svg>')
    return path


def _svg_line(points, title, path, w=680, h=320):
    if not points:
        return None
    pad = 48
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    xmn, xmx, ymx = min(xs), (max(xs) or 1), (max(ys) or 1)

    def X(x):
        return pad + (w - 2 * pad) * ((x - xmn) / ((xmx - xmn) or 1))

    def Y(y):
        return h - pad - (h - 2 * pad) * (y / ymx)

    pts = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in points)
    el = (f'<text x="{w/2}" y="18" font-size="13" text-anchor="middle" font-family="sans-serif">{title}</text>'
          f'<polyline fill="none" stroke="#4c78a8" stroke-width="2" points="{pts}"/>'
          f'<text x="{w/2}" y="{h-10}" font-size="10" text-anchor="middle" font-family="sans-serif">'
          f'minutes since first completion  ->  cumulative {ys[-1]}</text>')
    open(path, "w").write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">{el}</svg>')
    return path


def render_svgs(out, series):
    written = []
    for fn in [_svg_line(series["burnup"], "Diagnosis burn-up", os.path.join(out, "burnup.svg")),
               _svg_bar(series.get("turnaround_bars"), "Per-test turnaround (min buckets)", os.path.join(out, "turnaround.svg")),
               _svg_bar(series["workers"], "Units per worker", os.path.join(out, "workers.svg")),
               _svg_bar(series["causes"], "Root-cause classes", os.path.join(out, "causes.svg")),
               _svg_bar(series.get("cause_cat"), "Top cause categories", os.path.join(out, "cause_categories.svg")),
               _svg_bar(series.get("funnel"), "Funnel", os.path.join(out, "funnel.svg")),
               _svg_bar(series["clusters"], "Cluster sizes", os.path.join(out, "clusters.svg"))]:
        if fn:
            written.append(fn)
    return written


def _mm_pie(title, pairs):
    body = [f'  "{k}" : {v}' for k, v in pairs if v]
    if not body:
        return ""
    return "\n".join(["```mermaid", "pie showData", f"  title {title}", *body, "```"])


def _mm_bar(title, pairs, ylabel="count"):
    pairs = [(k, v) for k, v in pairs if v is not None]
    if not pairs:
        return ""
    labels = "[" + ", ".join(f'"{str(k)[:16]}"' for k, _ in pairs) + "]"
    vals = "[" + ", ".join(str(v) for _, v in pairs) + "]"
    mx = max(v for _, v in pairs) or 1
    return "\n".join(["```mermaid", "xychart-beta", f'  title "{title}"',
                      f"  x-axis {labels}", f'  y-axis "{ylabel}" 0 --> {mx}',
                      f"  bar {vals}", "```"])


def _mm_line(title, points, ylabel="cumulative"):
    if not points:
        return ""
    xs = "[" + ", ".join(f'"{int(x)}"' for x, _ in points) + "]"
    ys = "[" + ", ".join(str(y) for _, y in points) + "]"
    mx = max(y for _, y in points) or 1
    return "\n".join(["```mermaid", "xychart-beta", f'  title "{title}"',
                      f"  x-axis {xs}", f'  y-axis "{ylabel}" 0 --> {mx}',
                      f"  line {ys}", "```"])


def _downsample(points, k=20):
    if len(points) <= k:
        return points
    step = len(points) / k
    return [points[min(len(points) - 1, int(i * step))] for i in range(k)]


def main() -> int:
    ap = argparse.ArgumentParser(prog="metrics.py")
    ap.add_argument("db")
    ap.add_argument("--tfh", default=os.path.expanduser("~/.tfh"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--paste", action="store_true", help="submit the report as a Phabricator paste")
    ap.add_argument("--title", default="Test-fix harness run report")
    args = ap.parse_args()
    out = args.out or os.path.join(args.tfh, "report")
    os.makedirs(out, exist_ok=True)
    now = time.time()

    conn = sqlite3.connect(args.db)
    units = collect(conn, args.tfh)

    done = [u for u in units if u["state"] == "done"]
    durations = [u["duration"] for u in done if u["duration"] is not None]
    states = {}
    for u in units:
        states[u["state"]] = states.get(u["state"], 0) + 1
    n_units = len(units)
    n_issues = conn.execute("SELECT COUNT(*) FROM test_issue").fetchone()[0]

    # by-lane and by-category turnaround
    def stats(vals):
        return {"n": len(vals),
                "avg": round(statistics.mean(vals), 1) if vals else None,
                "median": round(statistics.median(vals), 1) if vals else None,
                "p90": round(sorted(vals)[int(.9 * (len(vals) - 1))], 1) if vals else None}

    by_lane = {}
    for lane in ("cpu", "single", "multi"):
        by_lane[lane] = stats([u["duration"] for u in done if u["gpu_req"] == lane and u["duration"]])
    by_cat = {}
    for cat in ("FAILURE", "FLAKY", "SKIPPING"):
        by_cat[cat] = stats([u["duration"] for u in done if u["category"] == cat and u["duration"]])

    # workers
    wc = {}
    for u in done:
        if u["worker_done"]:
            wc[u["worker_done"]] = wc.get(u["worker_done"], 0) + 1
    workers = sorted(wc.items(), key=lambda x: -x[1])

    # reproduction + outcomes + cause mix
    repro = {"reproduced": 0, "not_reproduced": 0, "flaky": 0}
    outcomes = {}
    cause_class = {k: 0 for k in FIX_CLASSES}
    cause_cat = {}
    for u in done:
        r = u["result"] or {}
        outcomes[r.get("status", "?")] = outcomes.get(r.get("status", "?"), 0) + 1
        if r.get("reproduced") is True:
            repro["reproduced"] += 1
        elif r.get("reproduced") is False:
            repro["not_reproduced"] += 1
        if r.get("flaky"):
            repro["flaky"] += 1
        for rc in (r.get("root_causes") or []):
            cat = rc.get("cause_category")
            cause_cat[cat] = cause_cat.get(cat, 0) + 1
            cause_class[classify_cause(cat)] += 1

    # churn: attempts + parks
    attempts_hist = {}
    for u in units:
        a = u["attempts"] or 0
        attempts_hist[a] = attempts_hist.get(a, 0) + 1
    parks = {}
    for u in units:
        if u["state"] == "parked":
            parks[u["park_category"] or "?"] = parks.get(u["park_category"] or "?", 0) + 1

    # Root-cause dedup computed from the diagnoses themselves (Stage-1 fingerprint
    # grouping) so it works before the formal reduce step persists root_causes.
    entries = [{"test_id": u["id"], "report": u["result"]} for u in done if u.get("result")]
    computed = reduce_mod.reduce(entries)
    real = [c for c in computed if not c["signature"].startswith("insufficient")]
    rc_total = sum(len((u["result"] or {}).get("root_causes") or []) for u in done)
    n_distinct = len(real)
    shared = [c for c in real if c["size"] > 1]
    tests_in_shared = sum(c["size"] for c in shared)
    dup_findings = max(0, rc_total - n_distinct)  # findings that merged into a shared cause
    collapse = round(rc_total / n_distinct, 2) if n_distinct else 0
    cluster_sizes = [(c["signature"][:10], c["size"]) for c in real]
    # ALL distinct root causes, ranked by tests affected (reduce() already sorts them)
    all_causes = [(c["signature"][:8], c["size"], classify_cause(c["cause_category"]),
                   c["cause_category"] or "?", (c["cause_summary"] or "")[:90]) for c in real]

    fn = funnel(conn, units)

    # burn-up series (minutes since first completion -> cumulative)
    comp_ts = sorted(u["complete_ts"] for u in done if u["complete_ts"])
    burnup = []
    if comp_ts:
        t0 = comp_ts[0]
        burnup = [(round((t - t0) / 60, 2), i + 1) for i, t in enumerate(comp_ts)]

    # ---- write CSVs ----
    def csv(name, header, rows):
        with open(os.path.join(out, name), "w") as fh:
            fh.write(header + "\n")
            for r in rows:
                fh.write(",".join(str(x) for x in r) + "\n")

    csv("completions.csv", "test,worker,lane,category,duration_s,reproduced,status",
        [(u["id"], u["worker_done"], u["gpu_req"], u["category"], u["duration"],
          (u["result"] or {}).get("reproduced"), (u["result"] or {}).get("status"))
         for u in done])
    csv("burnup.csv", "minutes,cumulative_done", burnup)
    csv("workers.csv", "worker,tests_done", workers)
    csv("cause_categories.csv", "cause_category,count", sorted(cause_cat.items(), key=lambda x: -x[1]))
    csv("funnel.csv", "stage,count", fn)
    if all_causes:
        csv("root_causes.csv", "signature,tests,fix_class,cause_category,summary",
            [(s, sz, fc, cat, summ.replace(",", ";")) for s, sz, fc, cat, summ in all_causes])

    # turnaround histogram buckets (minutes)
    turnaround_bars = []
    if durations:
        mxd = max(durations)
        nb = 10
        binsz = (mxd / nb) or 1
        hist = {}
        for d in durations:
            b = min(nb - 1, int(d // binsz))
            lbl = f"{int(b*binsz//60)}-{int((b+1)*binsz//60)}m"
            hist[lbl] = hist.get(lbl, 0) + 1
        turnaround_bars = sorted(hist.items(), key=lambda x: int(x[0].split("-")[0]))

    series = {"burnup": burnup, "durations": durations, "workers": workers,
              "causes": sorted(cause_class.items(), key=lambda x: -x[1]),
              "cause_cat": sorted(cause_cat.items(), key=lambda x: -x[1])[:10],
              "turnaround_bars": turnaround_bars, "funnel": fn,
              "clusters": cluster_sizes[:15]}
    charts = render_charts(out, series) + render_svgs(out, series)

    # ---- markdown report ----
    L = ["# Test-fix harness run report",
         f"_generated {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}_", ""]
    L += ["## Funnel"] + _bars(fn) + [""]
    L += ["## Diagnosis velocity",
          f"- diagnosed: {len(done)}   turnaround/test avg "
          f"{stats(durations)['avg']}s median {stats(durations)['median']}s p90 {stats(durations)['p90']}s",
          "- by lane: " + "  ".join(f"{k}(n={v['n']},med={v['median']}s)" for k, v in by_lane.items()),
          "- by category: " + "  ".join(f"{k}(n={v['n']},med={v['median']}s)" for k, v in by_cat.items()), ""]
    L += ["## Worker contribution"] + _bars(workers) + [""]
    L += ["## Reproduction + outcomes",
          f"- {repro}", f"- statuses: {outcomes}", ""]
    L += ["## Root-cause mix (code vs infra)"] + _bars(sorted(cause_class.items(), key=lambda x: -x[1])) + \
         ["", "top cause categories:"] + _bars(sorted(cause_cat.items(), key=lambda x: -x[1])[:8]) + [""]
    L += ["## Churn",
          f"- attempts histogram (attempts:tests): {dict(sorted(attempts_hist.items()))}",
          f"- parked: {parks or 'none'}", ""]
    L += ["## Root-cause dedup (from diagnoses; Stage-1 fingerprint)"]
    if n_distinct:
        L += [f"- {rc_total} root-cause findings -> {n_distinct} distinct causes "
              f"({dup_findings} duplicate findings merged; collapse {collapse}x)",
              f"- {len(shared)} causes are shared by >1 test, covering {tests_in_shared} tests",
              "", f"all {len(all_causes)} root causes, ranked by tests affected:",
              "  " + f"{'tests':>5}  {'fix-class':<8}  {'category':<16}  cause"]
        for sig, sz, fc, cat, summ in all_causes:
            L.append(f"  {sz:>5}  {fc:<8}  {cat:<16}  {summ}")
    else:
        L += ["- no diagnoses with a root cause yet"]
    L += [""]
    if charts:
        L += ["## Charts"] + [f"![{os.path.basename(c)}]({os.path.basename(c)})" for c in charts]
    else:
        L += ["## Charts", "_matplotlib not available; CSVs written for external plotting_"]
    rpt = os.path.join(out, "run-report.md")
    with open(rpt, "w") as fh:
        fh.write("\n".join(L) + "\n")

    # ---- paste-ready markdown with embedded mermaid charts (P2423329566 style) ----
    def _table(header, rows):
        out2 = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
        out2 += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
        return "\n".join(out2)

    P = [f"# {args.title}",
         f"_generated {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))} · "
         f"diagnosed {len(done)} / {n_units} tests / {n_issues} issues · "
         f"in-flight {states.get('dispatched', 0)} · queued {states.get('queued', 0)} · "
         f"parked {states.get('parked', 0)}_",
         "",
         f"> {n_issues} open issues collapse to {n_units} canonical tests (dedup); "
         f"all charts below are over the {n_units} tests. Completed-test charts sum to "
         f"'done' ({len(done)}), a subset while the run is in progress.", "",
         "## Funnel", "", _table(["stage", "count"], fn), "",
         "## Tests by state", "",
         _mm_pie("Tests by state", sorted(states.items(), key=lambda x: -x[1])), "",
         "## Root-cause mix (fix class)", "",
         _mm_pie("Fix class", sorted(cause_class.items(), key=lambda x: -x[1])), "",
         "## Reproduction", "",
         _mm_pie("Reproduction", list(repro.items())), "",
         f"## Worker contribution ({sum(c for _, c in workers)} of {n_units} tests done)", "",
         _mm_bar("Tests per worker", [(w.split(".")[0], c) for w, c in workers], "tests"), "",
         f"## Diagnosis burn-up (toward {n_units} tests)", "",
         _mm_line("Cumulative diagnosed", _downsample(burnup)), "",
         "## Per-test turnaround", "",
         _mm_bar("Turnaround (min buckets)", turnaround_bars, "tests"), "",
         "## Velocity detail", "",
         _table(["lane", "n", "median s", "p90 s"],
                [(k, v["n"], v["median"], v["p90"]) for k, v in by_lane.items()]), "",
         _table(["category", "n", "median s", "p90 s"],
                [(k, v["n"], v["median"], v["p90"]) for k, v in by_cat.items()]), "",
         "## Top cause categories", "",
         _table(["cause", "count"], sorted(cause_cat.items(), key=lambda x: -x[1])[:10]), "",
         "## Churn", "",
         f"attempts histogram (attempts:tests): `{dict(sorted(attempts_hist.items()))}`  ",
         f"parked: `{parks or 'none'}`", ""]
    if n_distinct:
        P += ["## Root-cause dedup (from diagnoses)", "",
              f"{rc_total} root-cause findings collapse to **{n_distinct} distinct causes** "
              f"({dup_findings} duplicate findings merged, collapse {collapse}x); "
              f"{len(shared)} causes shared by >1 test cover {tests_in_shared} tests.", "",
              f"**All {len(all_causes)} root causes, ranked by tests affected:**", "",
              _table(["tests", "fix-class", "category", "cause"],
                     [(sz, fc, cat, summ) for _, sz, fc, cat, summ in all_causes]), "",
              _mm_bar("Tests per root cause (top 15)", cluster_sizes[:15], "tests"), ""]
    paste_md = os.path.join(out, "paste-report.md")
    paste_content = "\n".join(P) + "\n"
    with open(paste_md, "w") as fh:
        fh.write(paste_content)

    if args.paste:
        import subprocess
        r = subprocess.run(
            ["meta", "phabricator.paste", "create", "--title", args.title,
             "--language", "markdown", "--content", paste_content, "--output", "json"],
            capture_output=True, text=True)
        print("paste:", (r.stdout.strip() or r.stderr.strip())[-600:])

    print(f"wrote {rpt}")
    print(f"wrote {paste_md}")
    print(f"charts: {[os.path.basename(c) for c in charts] or 'none (ascii in report)'}")
    print("\n".join(L[:40]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
