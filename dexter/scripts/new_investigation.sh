#!/usr/bin/env bash
# Generate a unique investigation ID and scaffold its artifact directory.
# Usage: new_investigation.sh <slug>
# Env:   INV_BASE (default: ~/workspace/investigations/cases)
set -euo pipefail

slug="${1:?usage: new_investigation.sh <slug>}"
base="${INV_BASE:-$HOME/workspace/investigations/cases}"
ts="$(date +%Y%m%d-%H%M%S)"
inv_id="${slug}-${ts}"
root="${base}/${inv_id}"

mkdir -p "$root"/{baseline/raw,jobs,code,results,knowledge-graph,hypotheses}

printf '# Investigation Journal — %s\n\nAppend-only chronological log. Never edit past entries.\n\n' "$inv_id" > "$root/JOURNAL.md"
printf '{"op":"meta","investigation_id":"%s","slug":"%s","created":"%s","status":"open"}\n' "$inv_id" "$slug" "$ts" > "$root/knowledge-graph/graph.jsonl"
printf '# Baseline — %s (STATIC)\n\nStatus: DRAFT (research in progress). FREEZE before Phase 3.\nDo NOT edit after finalization without explicit user confirmation.\n' "$inv_id" > "$root/baseline/BASELINE.md"
printf 'job_id\ttimestamp\thypothesis\tenv\tconfig\tresult\n' > "$root/jobs/INDEX.tsv"
echo "$inv_id" > "$root/.investigation_id"
echo "$inv_id" > "${base}/.current"

echo "INVESTIGATION_ID=$inv_id"
echo "ROOT=$root"
