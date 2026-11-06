#!/usr/bin/env python3
"""Self-check for kb.py, new_investigation.sh and record_job.sh: python3 scripts/test_kb.py"""
import hashlib, os, stat, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
d = tempfile.mkdtemp()
dh = tempfile.mkdtemp()  # DEXTER_HOME with a denylist and refs
empty_home = tempfile.mkdtemp()  # DEXTER_HOME with nothing in it
os.makedirs(os.path.join(d, "knowledge"))
H1, H2 = "2026-08-18 — Shrink the batch to split persistent from activation memory", "Launch cluster jobs detached"
open(os.path.join(d, "LESSONS.md"), "w").write(
    f"# Lessons\n\n## {H1}\n- batch body\n<!-- humanize-context\npurpose: zebra\n-->\n\n## {H2}\n- unrelated\n")
id1, id2 = (hashlib.sha1(h.encode()).hexdigest()[:6] for h in (H1, H2))


def run(cmd, home=dh, cwd=None, **env):
    e = {k: v for k, v in os.environ.items() if k not in ("INV_BASE", "CODE_DIFF")}
    e.update({"INVESTIGATIONS_DIR": d, "DEXTER_HOME": home, **env})
    return subprocess.run(cmd, env=e, cwd=cwd, capture_output=True, text=True)


def kb(*a, home=dh):
    return run([sys.executable, os.path.join(HERE, "kb.py"), *a], home=home)


out = kb("search", "batch").stdout
assert id1 in out and "Shrink the batch" in out and "detached" not in out, out
assert "(no matching lessons)" in kb("search", "zzqq").stdout
assert "(no matching lessons)" in kb("search", "atch").stdout  # word-start only: 'atch' must not hit 'batch'

r = kb("lesson", id1)
assert r.returncode == 0 and "batch body" in r.stdout and "LESSONS.md:3" in r.stdout, r
assert kb("lesson", "ffffff").returncode == 1
assert "(no matching lessons)" in kb("search", "zebra").stdout  # comment blocks are not lesson text

r = kb("digest")  # no digest file yet: both lessons missing
assert r.returncode == 1 and f"MISSING {id1}" in r.stdout and f"MISSING {id2}" in r.stdout, r
open(os.path.join(d, "LESSONS-DIGEST.md"), "w").write(f"# digest\n\n- {id1} OOM -> shrink batch\n- abc123 gone -> x\n")
r = kb("digest")
assert r.returncode == 1 and f"MISSING {id2}" in r.stdout and "ORPHAN  abc123" in r.stdout and f"MISSING {id1}" not in r.stdout, r
open(os.path.join(d, "LESSONS-DIGEST.md"), "w").write(f"- {id1} OOM -> shrink batch\n- {id2} cluster launch -> run detached\n")
r = kb("digest")
assert r.returncode == 0 and "covers all 2 lessons" in r.stdout, r

# --- validate: generic source_refs pass, private identifiers fail ---
SECTIONS = ["Symptom", "Root cause", "Fix", "Prevention", "Data points", "Generalizable lesson", "Verification"]


def entry(slug, body_extra="", ref="team incident review, 2026-05"):
    fm = (f"---\nid: {slug}\ntitle: Widget step time doubled after an upgrade\ndate: 2026-05-01\n"
          f"goal: restore step time\noutcome: goal-met - 10 s to 5 s\nsource: own_investigation\n"
          f"source_refs:\n  - {ref}\nenvironment:\n  org: work\n  surface: cluster job\n  hardware: 8xGPU\n"
          f"  workload: training\n  stack: PyTorch\ndomain: perf\ntags: [perf, widget]\nconfidence: 0.9\nstatus: confirmed\n---\n")
    secs = "".join(f"\n## {s}\nThe {s.lower()} section has real content, measured at 42 ms.{body_extra}\n" for s in SECTIONS)
    return fm + secs


def write(name, text, base=None):
    p = os.path.join(base or os.path.join(d, "knowledge"), name)
    open(p, "w").write(text)
    return p


v = tempfile.mkdtemp()  # entries validated outside the scanned knowledge dir
r = kb("validate", write("generic.md", entry("generic"), v))
assert r.returncode == 0, r.stdout

private = {
    "id": " See " + "D" + "12345678" + ".",
    "url": " See " + "http" + "s://example.org/x.",
    "path": " Ran in " + "/" + "home/someone/x.",
    "email": " Ask " + "someone" + "@" + "example.org.",
}
for kind, extra in private.items():
    r = kb("validate", write(f"p-{kind}.md", entry(f"p-{kind}", extra), v))
    assert r.returncode == 1 and "move to" in r.stdout and "refs" in r.stdout, (kind, r.stdout)
    assert extra.strip() not in r.stdout, (kind, "matched text must not be printed")
fn = "sev-" + "S" + "123456" + "-widget.md"
r = kb("validate", write(fn, entry("widget"), v))
assert r.returncode == 1 and "filename" in r.stdout, r.stdout
r = kb("validate", write("ref-id.md", entry("ref-id", ref="T" + "1234567"), v))
assert r.returncode == 1, r.stdout

# --- denylist: re:, ci and cs entries ---
CS, CI, RX = "Quux" + "corp", "blorp" + "net", "zz[0-9]+" + "q"
open(os.path.join(dh, "denylist.txt"), "w").write(f"{CS}\tcs\n{CI}\tci\nre:{RX}\tci\n")
for word, blocked in [(CS, True), (CS.lower(), False), (CI.upper(), True), ("ZZ12Q", True), ("plain", False)]:
    r = kb("validate", write("deny.md", entry("deny", f" Uses {word} here."), v))
    assert (r.returncode == 1) == blocked, (word, r.stdout)
    if blocked:
        assert "denylist" in r.stdout and word not in r.stdout, (word, r.stdout)

# --- scan: same checks over the investigations allowlist files ---
r = kb("scan")
assert r.returncode == 0, r.stdout
write("k1.md", entry("k1"))
assert kb("scan").returncode == 0
p = write("leak.md", entry("leak", f" Uses {CI.upper()} here."))
r = kb("scan")
assert r.returncode == 1 and "leak.md" in r.stdout and CI.upper() not in r.stdout, r.stdout
os.remove(p)
with open(os.path.join(d, "LESSONS.md"), "a") as f:
    f.write(f"- tried the {CS} path\n")
assert kb("scan").returncode == 1
open(os.path.join(d, "LESSONS.md"), "w").write(f"# Lessons\n\n## {H1}\n- batch body\n\n## {H2}\n- unrelated\n")
assert kb("scan").returncode == 0

# --- search merges refs/<id>.txt; index never carries refs text ---
os.makedirs(os.path.join(dh, "refs"))
open(os.path.join(dh, "refs", "k1.txt"), "w").write("frobnicator queue, ticket 42\n")
r = kb("search", "frobnicator")
assert "k1.md" in r.stdout and "refs: " + os.path.join(dh, "refs", "k1.txt") in r.stdout, r.stdout
r = kb("search", "frobnicator", home=empty_home)
assert "(no matching knowledge entries)" in r.stdout, r.stdout
assert kb("index").returncode == 0
assert "frobnicator" not in open(os.path.join(d, "KNOWLEDGE.md")).read()

# --- new_investigation.sh writes only under DEXTER_HOME ---
fake_home, dh2 = tempfile.mkdtemp(), os.path.join(tempfile.mkdtemp(), "dexter")
r = run(["bash", os.path.join(HERE, "new_investigation.sh"), "widget"], home=dh2, HOME=fake_home)
assert r.returncode == 0, r.stderr
root = [l.split("=", 1)[1] for l in r.stdout.splitlines() if l.startswith("ROOT=")][0]
assert root.startswith(os.path.join(dh2, "cases") + os.sep) and os.path.isdir(root), root
assert os.listdir(fake_home) == [], os.listdir(fake_home)
for p in (dh2, os.path.join(dh2, "cases"), root):
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o700, (p, oct(os.stat(p).st_mode))
assert stat.S_IMODE(os.stat(os.path.join(root, "JOURNAL.md")).st_mode) == 0o600

# --- record_job.sh snapshot guard ---
RJ = os.path.join(HERE, "record_job.sh")


def diff_text(path, line):
    return (f"dif" + f"f --git a/{path} b/{path}\n" + "--" + f"- a/{path}\n" + "++" + f"+ b/{path}\n"
            + "@" + "@ -1 +1 @@\n" + "-old\n" + line + "\n")


def record(job, home=dh, cwd=None, code_diff=None):
    env = {"CODE_DIFF": code_diff} if code_diff else {}
    r = run(["bash", RJ, root, job, "H1", "env", "cfg", "result"], home=home, cwd=cwd, **env)
    return r, os.path.exists(os.path.join(root, "code", f"{job}.diff"))


ok_diff = write("ok.diff", diff_text("src/ok.py", "+new"), v)
r, made = record("j-nodeny", home=empty_home, code_diff=ok_diff)
assert r.returncode != 0 and not made, r
r, made = record("j-ok", code_diff=ok_diff)
assert r.returncode == 0 and made, r
assert open(os.path.join(root, "code", "j-ok.diff")).read() == open(ok_diff).read()
r, made = record("j-path", code_diff=write("p.diff", diff_text(f"src/{CS}/a.py", "+new"), v))
assert r.returncode != 0 and not made, r
r, made = record("j-line", code_diff=write("l.diff", diff_text("src/ok.py", f"+uses {CI.upper()} here"), v))
assert r.returncode != 0 and not made, r

repo = tempfile.mkdtemp()
g = lambda *a: subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.org", *a],
                              check=True, capture_output=True)
g("init", "-q")
os.makedirs(os.path.join(repo, CS))
open(os.path.join(repo, CS, "a.py"), "w").write("old\n")
g("add", "-A")
g("commit", "-qm", "init")
open(os.path.join(repo, CS, "a.py"), "w").write("new\n")
r, made = record("j-wc", cwd=repo)
assert r.returncode != 0 and not made, r
print("ok")
