#!/usr/bin/env bash
# Worker-side unit runner (plan Slice 3). Invoked by the daemon over SSH:
#   run_unit.sh <unit_id> <expected_sha256>
# Verifies the scp'd payload, preflights the checkout, runs headless Claude Code
# under a hard timeout with an independent heartbeat, and writes result.json
# atomically (+ a sentinel-wrapped copy on stdout). Workers CANNOT land.
#
# Exit codes (transport outcome; the DIAGNOSTIC verdict lives in result.json.status):
#   0  protocol completed, valid result.json present
#   2  payload checksum mismatch / missing
#   3  environment not ready (wrong base_commit / claude missing)
#   4  timeout / killed
#   5  internal wrapper error
set -u

TFH_DIR="${TFH_DIR:-$HOME/.tfh}"
unit_id="${1:-}"; expected_sha="${2:-}"
[ -n "$unit_id" ] && [ -n "$expected_sha" ] || { echo "usage: run_unit.sh <unit_id> <sha256>" >&2; exit 5; }

unit_dir="$TFH_DIR/units/$unit_id"
payload="$unit_dir/payload.json"
result="$unit_dir/result.json"
hb="$unit_dir/heartbeat"

[ -f "$payload" ] || { echo "missing payload $payload" >&2; exit 2; }

# 1. Checksum: re-hash and compare (defends against truncated scp / tampering).
actual_sha=$(sha256sum "$payload" | awk '{print $1}')
if [ "$actual_sha" != "$expected_sha" ]; then
  echo "checksum mismatch: got $actual_sha want $expected_sha" >&2
  exit 2
fi

# 2. Preflight the pinned checkout unless explicitly skipped (tests set this).
base_commit=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("base_commit",""))' "$payload")
if [ "${TFH_SKIP_PREFLIGHT:-0}" != "1" ]; then
  command -v claude >/dev/null 2>&1 || { echo "claude not found" >&2; exit 3; }
  cur=$(sl id -i 2>/dev/null || true)
  if [ -n "$base_commit" ] && [ -n "$cur" ] && [ "$cur" != "$base_commit" ]; then
    sl goto "$base_commit" --clean --reason "tfh preflight - sl help goto" >/dev/null 2>&1 \
      || { echo "cannot reach base_commit $base_commit" >&2; exit 3; }
  fi
fi

# 3. Independent heartbeat ticker (safety net, 30s) regardless of Claude output.
( while :; do date -u +%FT%TZ > "$hb" 2>/dev/null; sleep 30; done ) &
hb_pid=$!
cleanup() { kill "$hb_pid" 2>/dev/null || true; }
trap cleanup EXIT

# 4. Build the scoped prompt from the phase template and run headless Claude Code
#    under a hard timeout. stdin redirected to skip the ~3s no-stdin wait.
phase=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("phase",""))' "$payload")
timeout_s=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("timeout_s",1800))' "$payload")
tmpl="$TFH_DIR/templates/phase_${phase}.md"

# In production the wrapper composes the template + payload into the prompt.
# STUB marker: the prompt assembly + result parsing land with Slice 4/5.
prompt="Follow $tmpl for unit $unit_id (payload at $payload). Write your JSON result to $result, and any supporting build/repro log to $unit_dir/evidence.txt (it is collected back to the master). Do NOT land."

if [ "${TFH_DRYRUN_FAKE_CLAUDE:-0}" = "1" ]; then
  # Test hook: skip the real model, emit a minimal completed result.
  printf '{"unit_id":"%s","status":"completed"}' "$unit_id" > "$result.tmp"
  mv "$result.tmp" "$result"
else
  timeout --signal=TERM --kill-after=30s "$timeout_s" \
    claude -p --dangerously-skip-permissions --max-turns 60 "$prompt" < /dev/null
  rc=$?
  [ "$rc" -eq 124 ] && { echo "unit timed out" >&2; exit 4; }
fi

[ -f "$result" ] || { echo "no result.json produced" >&2; exit 5; }
echo "<<<TFH_RESULT>>>"; cat "$result"; echo; echo "<<<END_RESULT>>>"
exit 0
