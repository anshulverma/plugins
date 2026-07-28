#!/usr/bin/env bash
# Land-guard shim (no-land backstop #2, ADR 0003/0005). Install by placing this
# file EARLIER in PATH under the names sl, jf, arc, hg (symlinks to this script).
# It hard-blocks land/push subcommands (logs + exits non-zero) and transparently
# passes everything else through to the real tool.
set -u

tool=$(basename "$0")
sub="${1:-}"
log="${TFH_LANDGUARD_LOG:-$HOME/.tfh/land_guard.log}"
mkdir -p "$(dirname "$log")" 2>/dev/null || true

case "$tool:$sub" in
  sl:land|jf:land|arc:land|hg:land|sl:push|hg:push)
    ts=$(date -u +%FT%TZ 2>/dev/null || echo now)
    echo "[land-guard] BLOCKED: $tool $*" >&2
    echo "$ts BLOCKED $tool $*" >> "$log" 2>/dev/null || true
    exit 97 ;;
esac

# Pass through: find the real binary (first PATH hit that is not this shim dir).
shimdir=$(cd "$(dirname "$0")" && pwd)
real=""
for cand in $(which -a "$tool" 2>/dev/null); do
  d=$(cd "$(dirname "$cand")" 2>/dev/null && pwd)
  if [ "$d" != "$shimdir" ]; then real="$cand"; break; fi
done
if [ -z "$real" ]; then
  echo "[land-guard] real '$tool' not found on PATH" >&2
  exit 127
fi
exec "$real" "$@"
