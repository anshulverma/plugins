#!/usr/bin/env bash
# Record a test job: append to jobs/INDEX.tsv, create jobs/<job_id>.md, and
# (best-effort) snapshot the current working-copy diff to code/<job_id>.diff.
# Usage: record_job.sh <INV_ROOT> <job_id> <hypothesis_id> "<env>" "<config>" "<one-line result>"
# Optional env: CODE_DIFF=<path-to-a-diff-file> to snapshot explicitly instead of auto sl/git diff.
set -euo pipefail
root="${1:?INV_ROOT}"; job="${2:?job_id}"; hyp="${3:?hypothesis_id}"
env_desc="${4:-}"; config="${5:-}"; result="${6:-}"
ts="$(date +%Y-%m-%dT%H:%M:%S%z)"

printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$job" "$ts" "$hyp" "$env_desc" "$config" "$result" >> "$root/jobs/INDEX.tsv"

cat > "$root/jobs/${job}.md" <<EOF
# Job ${job}

- timestamp: ${ts}
- hypothesis: ${hyp}
- env: ${env_desc}
- config: ${config}
- result (one line): ${result}
- code snapshot: code/${job}.diff
- parsed result: results/${job}.md

## Raw metrics

(paste raw metric output here)
EOF

# Best-effort code snapshot.
diff_out="$root/code/${job}.diff"
if [[ -n "${CODE_DIFF:-}" && -f "${CODE_DIFF}" ]]; then
  cp "${CODE_DIFF}" "$diff_out"
elif command -v sl >/dev/null 2>&1 && sl root >/dev/null 2>&1; then
  sl diff --reason "snapshot code for perf-investigation job ${job} - sl help diff" > "$diff_out" 2>/dev/null || : > "$diff_out"
elif command -v git >/dev/null 2>&1 && git rev-parse >/dev/null 2>&1; then
  git diff > "$diff_out" 2>/dev/null || : > "$diff_out"
else
  : > "$diff_out"
fi

printf -- '- **%s** recorded job `%s` (hyp %s): %s\n' "$ts" "$job" "$hyp" "$result" >> "$root/JOURNAL.md"
echo "recorded job $job -> $root/jobs/${job}.md"
