#!/usr/bin/env bash
# Append a timestamped line to the investigation JOURNAL.
# Usage: log.sh <INV_ROOT> "<message>"
set -euo pipefail
root="${1:?usage: log.sh <INV_ROOT> <message>}"
msg="${2:?usage: log.sh <INV_ROOT> <message>}"
ts="$(date +%Y-%m-%dT%H:%M:%S%z)"
printf -- '- **%s** %s\n' "$ts" "$msg" >> "$root/JOURNAL.md"
echo "logged: $msg"
