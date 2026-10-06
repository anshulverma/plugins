#!/usr/bin/env python3
"""Self-check for kb.py's lesson commands (search, lesson, digest): python3 scripts/test_kb.py"""
import hashlib, os, subprocess, sys, tempfile

d = tempfile.mkdtemp()
os.makedirs(os.path.join(d, "knowledge"))
H1, H2 = "2026-08-18 — Shrink the batch to split persistent from activation memory", "Launch MAST jobs detached"
open(os.path.join(d, "LESSONS.md"), "w").write(f"# Lessons\n\n## {H1}\n- batch body\n\n## {H2}\n- unrelated\n")
id1, id2 = (hashlib.sha1(h.encode()).hexdigest()[:6] for h in (H1, H2))


def kb(*a):
    return subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb.py"), *a],
                          env={**os.environ, "INVESTIGATIONS_DIR": d}, capture_output=True, text=True)


out = kb("search", "batch").stdout
assert id1 in out and "Shrink the batch" in out and "detached" not in out, out
assert "(no matching lessons)" in kb("search", "zzqq").stdout
assert "(no matching lessons)" in kb("search", "atch").stdout  # word-start only: 'atch' must not hit 'batch'

r = kb("lesson", id1)
assert r.returncode == 0 and "batch body" in r.stdout and "LESSONS.md:3" in r.stdout, r
assert kb("lesson", "ffffff").returncode == 1

r = kb("digest")  # no digest file yet: both lessons missing
assert r.returncode == 1 and f"MISSING {id1}" in r.stdout and f"MISSING {id2}" in r.stdout, r
open(os.path.join(d, "LESSONS-DIGEST.md"), "w").write(f"# digest\n\n- {id1} OOM -> shrink batch\n- abc123 gone -> x\n")
r = kb("digest")
assert r.returncode == 1 and f"MISSING {id2}" in r.stdout and "ORPHAN  abc123" in r.stdout and f"MISSING {id1}" not in r.stdout, r
open(os.path.join(d, "LESSONS-DIGEST.md"), "w").write(f"- {id1} OOM -> shrink batch\n- {id2} MAST launch -> run detached\n")
r = kb("digest")
assert r.returncode == 0 and "covers all 2 lessons" in r.stdout, r
print("ok")
