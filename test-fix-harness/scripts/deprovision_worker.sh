#!/usr/bin/env bash
# Deprovision a worker at run end (plan Slice 9). Soft by default: abandon
# uncommitted local work, KEEP published diffs and the EdenFS checkout (OD
# idle-timeout reclaims the host). --hard runs `eden rm` to reclaim capacity now.
# STUB: structure only.
#   deprovision_worker.sh <host> [--hard]
set -u
HOST="${1:?usage: deprovision_worker.sh <host> [--hard]}"
MODE="${2:-}"

echo "[deprovision] $HOST mode=${MODE:-soft} (stub)"
# soft:
#   ssh "$HOST" 'sl clean --all 2>/dev/null; sl goto <pinned> --clean'  # drop local edits
#   leave published Phabricator diffs and the checkout intact
# --hard:
#   [ "$MODE" = "--hard" ] && ssh "$HOST" 'eden rm --yes <checkout>'    # reclaim capacity
exit 0
