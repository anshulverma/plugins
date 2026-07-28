#!/usr/bin/env bash
# Verify a bootstrapped worker before adding it to hosts.txt (plan Slice 9).
# Emits one JSON object with a boolean per check; the daemon admits the worker
# only if all are true. STUB: structure + checks; wire the real assertions in.
#   verify_worker.sh <host> <pinned_commit>
set -u
HOST="${1:?usage: verify_worker.sh <host> <pinned_commit>}"
PINNED="${2:?usage: verify_worker.sh <host> <pinned_commit>}"

# Each check runs over SSH from the master's real login shell (has SSH cert).
# TODO wire real remote commands; emit conservative false until implemented.
cat <<JSON
{
  "host": "$HOST",
  "commit_ok": false,          // ssh $HOST 'sl id -i' == $PINNED
  "claude_ok": false,          // claude --version == pinned AND 'claude -p ping' returns
  "testx_debug_ok": false,     // ~/.claude/skills/testx-debug present (dotsync2), hash matches
  "warm_buck_ok": false,       // ~/.tfh/warm_buck.done present (full or partial)
  "templates_ok": false,       // ~/.tfh/templates/phase_A.md and phase_B.md present
  "land_guard_ok": false       // land-guard shims earlier on PATH; 'sl land' exits 97
}
JSON
exit 0
