#!/usr/bin/env bash
# Record a test job: append to jobs/INDEX.tsv, create jobs/<job_id>.md, and
# snapshot the current working-copy diff to code/<job_id>.diff.
# Usage: record_job.sh <INV_ROOT> <job_id> <hypothesis_id> "<env>" "<config>" "<one-line result>"
# Optional env: CODE_DIFF=<path-to-a-diff-file> to snapshot explicitly instead of auto git/sl diff.
# The snapshot is refused (exit 3, no code/<job_id>.diff) when $DEXTER_HOME/denylist.txt is missing
# or empty, or when any snapshot path, diff header path or content line matches a denylist term.
set -euo pipefail
umask 077
root="${1:?INV_ROOT}"; job="${2:?job_id}"; hyp="${3:?hypothesis_id}"
env_desc="${4:-}"; config="${5:-}"; result="${6:-}"
ts="$(date +%Y-%m-%dT%H:%M:%S%z)"

snap_cmd=(); paths=()
if [[ -n "${CODE_DIFF:-}" && -f "${CODE_DIFF}" ]]; then
  snap_cmd=(cat "${CODE_DIFF}"); paths=("${CODE_DIFF}")
elif command -v git >/dev/null 2>&1 && git rev-parse >/dev/null 2>&1; then
  snap_cmd=(git diff)
elif command -v sl >/dev/null 2>&1 && sl root >/dev/null 2>&1; then
  snap_cmd=(sl diff --reason "snapshot code for perf-investigation job ${job} - sl help diff")
fi
# The diff is checked in memory and written only if clean, so a refused diff is never copied to disk.
snap="none (no diff source)"; rc=0
if [[ ${#snap_cmd[@]} -gt 0 ]]; then
  if { "${snap_cmd[@]}" 2>/dev/null || true; } |
      python3 "$(dirname "$0")/kb.py" diffcheck - ${paths[@]+"${paths[@]}"} --out "$root/code/${job}.diff"; then
    snap="code/${job}.diff"
  else
    snap="refused (denylist missing or matched)"; rc=3
    echo "record_job: code snapshot refused for job ${job}" >&2
  fi
fi

printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$job" "$ts" "$hyp" "$env_desc" "$config" "$result" >> "$root/jobs/INDEX.tsv"

cat > "$root/jobs/${job}.md" <<EOF
# Job ${job}

- timestamp: ${ts}
- hypothesis: ${hyp}
- env: ${env_desc}
- config: ${config}
- result (one line): ${result}
- code snapshot: ${snap}
- parsed result: results/${job}.md

## Raw metrics

(paste raw metric output here)
EOF

printf -- '- **%s** recorded job `%s` (hyp %s): %s\n' "$ts" "$job" "$hyp" "$result" >> "$root/JOURNAL.md"
echo "recorded job $job -> $root/jobs/${job}.md"
exit "$rc"
