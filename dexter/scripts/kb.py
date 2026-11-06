#!/usr/bin/env python3
"""Structured knowledge base for dexter investigations.

Entries live in $INVESTIGATIONS_DIR/knowledge/<slug>.md (default
~/workspace/investigations). Each entry is a confirmed learning (own investigation
or an ingested external RCA, incident review or postmortem) conforming to
references/KNOWLEDGE-SCHEMA.md. Entries hold generic text only; real identifiers
(incident, diff, task and job ids, URLs, internal names) go in the private refs
file $DEXTER_HOME/refs/<slug>.txt, which `search` also reads.
Read ~/.claude/LOCAL.md if present for local names and details.

The point of `validate` is to guarantee NO HOLES: future investigations rely on
these entries, so a vague/evidence-free entry is rejected.

Commands:
  kb.py validate <entry.md>      # enforce the schema; exit!=0 and list gaps if invalid
  kb.py search <terms...>        # rank entries (tags/domain/env/title/body), then LESSONS.md lessons
  kb.py lesson <id...>           # print full LESSONS.md lessons by id (ids shown by search/digest)
  kb.py digest                   # check LESSONS-DIGEST.md has one line per lesson; list missing ones
  kb.py index                    # regenerate KNOWLEDGE.md
  kb.py scan                     # privacy-check every file the investigations repo may hold; exit!=0 on a hit
  kb.py diffcheck <diff|-> [path...] [--out F] # record_job.sh: write F only if no denylist term
  kb.py template <slug>          # print a blank schema-conformant entry to fill
Env: INVESTIGATIONS_DIR (default ~/workspace/investigations)
     DEXTER_HOME (default ~/.dexter): refs/<slug>.txt and denylist.txt (lines `term<TAB>cs|ci`, `re:` = regex)
"""
from __future__ import annotations

import hashlib
import os
import re
import sys

REQUIRED_FM = ["id", "title", "date", "goal", "outcome", "source", "source_refs", "environment", "domain", "tags", "confidence", "status"]
REQUIRED_ENV = ["org", "surface", "hardware", "workload", "stack"]
ORGS = ("work", "personal", "external")
REQUIRED_SECTIONS = ["Symptom", "Root cause", "Fix", "Prevention", "Data points", "Generalizable lesson", "Verification"]
PLACEHOLDERS = ["tbd", "todo", "???", "fixme", "xxx"]


def base_dir() -> str:
    return os.environ.get("INVESTIGATIONS_DIR", os.path.expanduser("~/workspace/investigations"))


def knowledge_dir() -> str:
    return os.path.join(base_dir(), "knowledge")


def dexter_home() -> str:
    return os.environ.get("DEXTER_HOME") or os.path.expanduser("~/.dexter")


def refs_path(slug: str) -> str:
    return os.path.join(dexter_home(), "refs", slug + ".txt")


PRIVATE = [
    ("identifier", re.compile(r"\b[DTPS]\d{5,}\b")),
    ("URL", re.compile(r"https?://")),
    ("absolute home/data path", re.compile(r"(?<![\w.~-])/(?:home|Users|data|root)/")),
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
]


def load_denylist() -> list | None:
    """Compiled patterns from $DEXTER_HOME/denylist.txt, or None if missing or empty."""
    path = os.path.join(dexter_home(), "denylist.txt")
    if not os.path.isfile(path):
        return None
    pats = []
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        term, _, flag = line.rstrip("\n").partition("\t")
        if not term.strip():
            continue
        rx = term[3:] if term.startswith("re:") else \
            r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?:s|es)?(?![A-Za-z0-9_])"
        try:
            pats.append(re.compile(rx, 0 if flag.strip() == "cs" else re.I))
        except re.error:
            sys.exit(f"denylist line {n}: invalid regex")
    return pats or None


def private_hits(text: str, deny: list | None) -> list[tuple[int, str]]:
    """(line number, kind) for each private match. Never returns the matched text."""
    hits = []
    for n, line in enumerate(text.split("\n"), 1):
        hits += [(n, kind) for kind, rx in PRIVATE if rx.search(line)]
        if deny and any(rx.search(line) for rx in deny):
            hits.append((n, "denylist term"))
    return hits


def _split_path(p: str) -> str:
    return re.sub(r"[/._-]", " ", p)


def private_problems(path: str, text: str, deny: list | None) -> list[str]:
    slug = os.path.splitext(os.path.basename(path))[0]
    where = f"move to $DEXTER_HOME/refs/{slug}.txt"
    name = os.path.basename(path)
    probs = [f"filename: private {kind}; rename with a generic slug" for kind in
             sorted({k for _, k in private_hits(name + "\n" + _split_path(name), deny)})]
    probs += [f"line {n}: private {kind}; {where}" for n, kind in private_hits(text, deny)]
    return probs


def _strip_comments(text: str) -> str:
    """Drop HTML comments such as humanize's context block: metadata, not entry content."""
    return re.sub(r"(?s)<!--.*?-->", "", text)


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    if end == -1:
        return "", text
    fm = text[3:end].strip("\n")
    body = text[end + 4:]
    return fm, body


def _parse_fm(fm: str) -> dict:
    """Minimal indent-aware parser for this schema's frontmatter (no PyYAML dep)."""
    out: dict = {}
    lines = fm.split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        if not raw.startswith(" ") and ":" in raw:
            key, _, val = raw.partition(":")
            key = key.strip()
            val = val.strip()
            if val == "":
                # could be a nested map or a list; look ahead
                block = []
                j = i + 1
                while j < len(lines) and (lines[j].startswith(" ") or not lines[j].strip()):
                    if lines[j].strip():
                        block.append(lines[j])
                    j += 1
                if block and block[0].lstrip().startswith("- "):
                    out[key] = [b.lstrip()[2:].strip() for b in block if b.lstrip().startswith("- ")]
                else:
                    sub = {}
                    for b in block:
                        if ":" in b:
                            sk, _, sv = b.strip().partition(":")
                            sub[sk.strip()] = sv.strip()
                    out[key] = sub
                i = j
                continue
            else:
                if val.startswith("[") and val.endswith("]"):
                    inner = val[1:-1].strip()
                    out[key] = [x.strip() for x in inner.split(",") if x.strip()] if inner else []
                else:
                    out[key] = val
        i += 1
    return out


def _sections(body: str) -> dict:
    secs: dict = {}
    cur = None
    buf: list = []
    for line in body.split("\n"):
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if cur is not None:
                secs[cur] = "\n".join(buf).strip()
            cur = m.group(1).strip()
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        secs[cur] = "\n".join(buf).strip()
    return secs


def validate(path: str) -> list[str]:
    problems: list[str] = []
    try:
        raw = open(path, encoding="utf-8").read()
    except OSError as e:
        return [f"cannot read {path}: {e}"]
    problems += private_problems(path, raw, load_denylist())
    text = _strip_comments(raw)
    fm_raw, body = _split_frontmatter(text)
    if not fm_raw:
        return problems + ["missing YAML frontmatter (--- ... ---)"]
    fm = _parse_fm(fm_raw)

    for k in REQUIRED_FM:
        if k not in fm or fm[k] in ("", [], {}, None):
            problems.append(f"frontmatter: missing/empty '{k}'")
    if isinstance(fm.get("source_refs"), list) and len(fm["source_refs"]) < 1:
        problems.append("frontmatter: source_refs needs >=1 generic source description")
    env = fm.get("environment")
    if isinstance(env, dict):
        for sk in REQUIRED_ENV:
            if not env.get(sk):
                problems.append(f"environment: missing/empty '{sk}'")
        if env.get("org") and env["org"] not in ORGS:
            problems.append(f"environment: org must be one of {' | '.join(ORGS)}")
    elif "environment" in fm:
        problems.append("environment: must be a map with org/surface/hardware/workload/stack")

    secs = _sections(body)
    for s in REQUIRED_SECTIONS:
        if s not in secs:
            problems.append(f"body: missing section '## {s}'")
        elif len(secs[s]) < 25:
            problems.append(f"body: section '## {s}' is a stub (<25 chars) — fill it")

    dp = secs.get("Data points", "")
    if dp and not re.search(r"\d", dp):
        problems.append("body: '## Data points' has no concrete number/metric")

    low = text.lower()
    for p in PLACEHOLDERS:
        if p in low:
            problems.append(f"placeholder token '{p}' present — no holes allowed")
    # allow N/A only under environment.hardware
    for m in re.finditer(r"\bn/a\b", low):
        ctx = low[max(0, m.start() - 40):m.start()]
        if "hardware" not in ctx:
            problems.append("'N/A' allowed only for environment.hardware")
            break
    return problems


def cmd_validate(args):
    path = args[0]
    probs = validate(path)
    if probs:
        print(f"INVALID: {path}")
        for p in probs:
            print(f"  - {p}")
        sys.exit(1)
    print(f"OK: {path} passes the knowledge quality bar")


def _load_all() -> list[dict]:
    d = knowledge_dir()
    out = []
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".md"):
            continue
        path = os.path.join(d, fn)
        fm_raw, body = _split_frontmatter(_strip_comments(open(path, encoding="utf-8").read()))
        fm = _parse_fm(fm_raw) if fm_raw else {}
        refs = refs_path(fn[:-3])
        refs = open(refs, encoding="utf-8").read() if os.path.isfile(refs) else ""
        out.append({"path": path, "fn": fn, "fm": fm, "body": body, "refs": refs})
    return out


def _hits(text: str, term: str) -> int:
    """Count matches starting at a word boundary, so 'hang' matches 'hanging' but not 'change'."""
    return len(re.findall(r"(?<![a-z0-9_])" + re.escape(term), text))


def cmd_search(args):
    terms = [t.lower() for t in args]
    entries = _load_all()
    scored = []
    for e in entries:
        fm = e["fm"]
        hay = " ".join([
            str(fm.get("title", "")), str(fm.get("domain", "")),
            " ".join(fm.get("tags", []) if isinstance(fm.get("tags"), list) else []),
            " ".join(str(v) for v in (fm.get("environment") or {}).values()),
            e["body"], e["refs"],
        ]).lower()
        score = sum(_hits(hay, t) for t in terms) if terms else 0
        # weight tag/domain/title hits higher
        head = (str(fm.get("title", "")) + " " + str(fm.get("domain", "")) + " " +
                " ".join(fm.get("tags", []) if isinstance(fm.get("tags"), list) else [])).lower()
        score += 5 * sum(_hits(head, t) for t in terms)
        if score > 0 or not terms:
            scored.append((score, e))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        print("(no matching knowledge entries)")
    for score, e in scored[:10]:
        fm = e["fm"]
        env = fm.get("environment") or {}
        print(f"[{score}] {fm.get('title','(untitled)')}")
        print(f"      {e['path']}")
        print(f"      domain={fm.get('domain','?')} env={env.get('org','?')}/{env.get('surface','?')} tags={fm.get('tags','')}")
        if e["refs"]:
            print(f"      refs: {refs_path(e['fn'][:-3])}")
    if terms:
        _search_lessons(terms)


def lessons_path() -> str:
    return os.path.join(base_dir(), "LESSONS.md")


def digest_path() -> str:
    return os.path.join(base_dir(), "LESSONS-DIGEST.md")


def _load_lessons() -> list[dict]:
    """One lesson per '## ' heading in LESSONS.md. The id hashes the heading, so it survives
    new lessons being added at the top (line numbers shift; append-only headings don't)."""
    if not os.path.isfile(lessons_path()):
        return []
    lessons, cur, in_comment = [], None, False
    for n, line in enumerate(open(lessons_path(), encoding="utf-8"), 1):
        # Skip whole-line HTML comments (humanize-context blocks) but keep line numbers.
        if in_comment or line.lstrip().startswith("<!--"):
            in_comment = "-->" not in line
            continue
        if line.startswith("## "):
            head = line[3:].strip()
            cur = {"id": hashlib.sha1(head.encode()).hexdigest()[:6], "line": n, "head": head, "body": []}
            lessons.append(cur)
        elif cur:
            cur["body"].append(line)
    return lessons


def _search_lessons(terms: list[str]) -> None:
    """Rank lessons like knowledge entries; heading hits weigh 6x."""
    scored = []
    for l in _load_lessons():
        head, body = l["head"].lower(), "".join(l["body"]).lower()
        score = sum(_hits(body, t) + 6 * _hits(head, t) for t in terms)
        if score > 0:
            scored.append((score, l))
    scored.sort(key=lambda x: -x[0])
    print("\nLessons (full text: kb.py lesson <id>):")
    if not scored:
        print("(no matching lessons)")
    for score, l in scored[:10]:
        print(f"[{score}] {l['id']} {l['head']}")


def cmd_lesson(args):
    lessons = {l["id"]: l for l in _load_lessons()}
    missing = [i for i in args if i not in lessons]
    for i in args:
        if i in lessons:
            l = lessons[i]
            print(f"## {l['head']}  ({lessons_path()}:{l['line']})")
            print("".join(l["body"]).strip() + "\n")
    if missing or not args:
        print(f"unknown lesson id(s): {' '.join(missing) or '(none given)'}", file=sys.stderr)
        sys.exit(1)


def cmd_digest(args):
    """Check LESSONS-DIGEST.md has exactly one line per lesson; list what to add or remove."""
    lessons = _load_lessons()
    have = set(re.findall(r"^- ([0-9a-f]{6}) ", open(digest_path(), encoding="utf-8").read(), re.M)) \
        if os.path.isfile(digest_path()) else set()
    missing = [l for l in lessons if l["id"] not in have]
    orphans = have - {l["id"] for l in lessons}
    for l in missing:
        print(f"MISSING {l['id']}  {l['head']}  ({lessons_path()}:{l['line']})")
    for i in sorted(orphans):
        print(f"ORPHAN  {i}  (no lesson has this id; remove the line)")
    if missing or orphans:
        print(f"digest stale: add a line per MISSING lesson to {digest_path()} as '- <id> <when -> do, <=25 words>'")
        sys.exit(1)
    print(f"OK: {digest_path()} covers all {len(lessons)} lessons")


def cmd_index(args):
    entries = _load_all()
    lines = ["# Knowledge base index", "",
             f"{len(entries)} entries. One learning per file in `knowledge/`. See `dexter` plugin references/KNOWLEDGE-SCHEMA.md.", ""]
    for e in entries:
        fm = e["fm"]
        env = fm.get("environment") or {}
        tags = ",".join(fm.get("tags", []) if isinstance(fm.get("tags"), list) else [])
        lines.append(f"- [{fm.get('title','(untitled)')}](knowledge/{e['fn']}) — {fm.get('domain','?')} · {env.get('org','?')}/{env.get('surface','?')} · {tags}")
    out = os.path.join(base_dir(), "KNOWLEDGE.md")
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"indexed {len(entries)} entries -> {out}")


def cmd_scan(args):
    """Privacy-check the files the investigations repo may hold (path allowlist)."""
    deny, base, probs = load_denylist(), base_dir(), []
    kd = knowledge_dir()
    files = [os.path.join(kd, f) for f in sorted(os.listdir(kd)) if f.endswith(".md")] if os.path.isdir(kd) else []
    files += [os.path.join(base, f) for f in ("LESSONS.md", "LESSONS-DIGEST.md", "KNOWLEDGE.md", "README.md", "CLAUDE.md", ".gitignore")]
    for path in files:
        if os.path.isfile(path):
            probs += [f"{path}: {p}" for p in private_problems(path, open(path, encoding="utf-8").read(), deny)]
    for p in probs:
        print(p)
    if probs:
        print(f"PRIVATE: {len(probs)} hit(s); write generic text and move real ids to $DEXTER_HOME/refs/<slug>.txt")
        sys.exit(1)
    print(f"OK: no private identifiers in {base}" + ("" if deny else " (no denylist: only id/URL/path/email checks ran)"))


def cmd_diffcheck(args):
    """diffcheck <diff|-> [path...] [--out FILE]: exit 0 only if the diff and the paths hold no
    denylist term, then write the diff to FILE; 1 on a match, 2 if there is no denylist."""
    out = None
    if "--out" in args:
        i = args.index("--out")
        out, args = args[i + 1], args[:i] + args[i + 2:]
    data = sys.stdin.read() if args[0] == "-" else open(args[0], encoding="utf-8", errors="replace").read()
    deny = load_denylist()
    if not deny:
        print(f"no denylist at {dexter_home()}/denylist.txt", file=sys.stderr)
        sys.exit(2)
    lines, paths = data.split("\n"), list(args[1:])
    for line in lines:
        if line.startswith(("diff --git ", "--- ", "+++ ")):
            paths += [re.sub(r"^[ab]/", "", t) for t in line.split()[2 if line.startswith("diff") else 1:]]
    hits = [f"path {i + 1}" for i, p in enumerate(paths) if any(rx.search(p) or rx.search(_split_path(p)) for rx in deny)]
    hits += [f"line {n}" for n, line in enumerate(lines, 1) if any(rx.search(line) for rx in deny)]
    if hits:
        print(f"denylist term in snapshot: {', '.join(hits[:10])}", file=sys.stderr)
        sys.exit(1)
    if out:
        with open(os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            f.write(data)


def cmd_template(args):
    slug = args[0] if args else "my-learning-slug"
    print(f"""---
id: {slug}
title: <one concrete sentence>
date: <YYYY-MM-DD>
goal: <what the investigation aimed to achieve + definition-of-done>
outcome: <goal-met | partial | handed-off | understand-only> - <before->after or what remains>
source: own_investigation
source_refs:
  - <generic description, e.g. team incident review, 2026-05; real ids go in $DEXTER_HOME/refs/{slug}.txt>
environment:
  org: <work | personal | external>
  surface: <monorepo | cluster job | dev server | prod-service | ...>
  hardware: <e.g. 32xH100 | dev-server-96cpu | N/A>
  workload: <e.g. MoE SFT training | RPC service | ...>
  stack: <e.g. PyTorch/FSDP | python/Buck2 | ...>
domain: <short area>
tags: [<keywords>]
confidence: 0.9
status: confirmed
---

## Symptom
<observed behavior + concrete data points>

## Root cause
<confirmed mechanism + evidence; why alternatives excluded>

## Fix
<what changed, described generically>

## Prevention
<guardrails/alerts/tests/lint to avoid reoccurrence, or name the gap>

## Data points
<>=1 concrete number/metric>

## Generalizable lesson
<transferable heuristic for future, possibly different, investigations>

## Verification
<how root cause + fix were confirmed>
""")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    cmd, rest = sys.argv[1], sys.argv[2:]
    {"validate": cmd_validate, "search": cmd_search, "lesson": cmd_lesson, "digest": cmd_digest, "index": cmd_index,
     "scan": cmd_scan, "diffcheck": cmd_diffcheck, "template": cmd_template}.get(
        cmd, lambda a: (print(f"unknown command {cmd}"), sys.exit(2))
    )(rest)


if __name__ == "__main__":
    main()
