#!/usr/bin/env bash
# Slice 3 shell test: land-guard block + run_unit.sh checksum/happy-path exits.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
fail=0
check() { if [ "$1" = "$2" ]; then echo "ok: $3"; else echo "FAIL: $3 (got $1 want $2)"; fail=1; fi; }

# --- land-guard: blocked subcommands exit non-zero and log ---
guarddir=$(mktemp -d)
for t in sl jf arc hg; do cp "$HERE/land_guard.sh" "$guarddir/$t"; chmod +x "$guarddir/$t"; done
export TFH_LANDGUARD_LOG="$guarddir/land.log"

PATH="$guarddir:$PATH" sl land >/dev/null 2>&1; check "$?" "97" "sl land blocked"
PATH="$guarddir:$PATH" jf land >/dev/null 2>&1; check "$?" "97" "jf land blocked"
PATH="$guarddir:$PATH" hg push >/dev/null 2>&1; check "$?" "97" "hg push blocked"
grep -q "BLOCKED sl land" "$TFH_LANDGUARD_LOG"; check "$?" "0" "land-guard logged the block"

# --- run_unit.sh: checksum mismatch -> exit 2 ---
export TFH_DIR=$(mktemp -d)
mkdir -p "$TFH_DIR/units/u1"
echo '{"phase":"A","base_commit":"","timeout_s":60}' > "$TFH_DIR/units/u1/payload.json"
bash "$HERE/run_unit.sh" u1 deadbeefwrong >/dev/null 2>&1; check "$?" "2" "checksum mismatch exits 2"

# --- run_unit.sh: correct checksum + faked claude -> exit 0 + result.json ---
good_sha=$(sha256sum "$TFH_DIR/units/u1/payload.json" | awk '{print $1}')
TFH_SKIP_PREFLIGHT=1 TFH_DRYRUN_FAKE_CLAUDE=1 bash "$HERE/run_unit.sh" u1 "$good_sha" >/tmp/tfh_ru.out 2>&1
check "$?" "0" "happy path exits 0"
grep -q "<<<TFH_RESULT>>>" /tmp/tfh_ru.out; check "$?" "0" "emits result sentinel"

if [ "$fail" = "0" ]; then echo "PASS test_worker"; else echo "FAILED test_worker"; fi
exit "$fail"
