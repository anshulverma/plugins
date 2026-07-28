#!/usr/bin/env bash
# Run the full harness test suite. Exit non-zero if any test fails.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
fail=0
for t in test_schema test_triage test_contracts test_templates \
         test_dispatcher test_scheduler test_reduce test_dryrun test_observability; do
  if python3 "$t.py" >/dev/null 2>&1; then echo "PASS $t"; else echo "FAIL $t"; fail=1; fi
done
if bash test_worker.sh >/dev/null 2>&1; then echo "PASS test_worker"; else echo "FAIL test_worker"; fail=1; fi
[ "$fail" = 0 ] && echo "ALL GREEN" || echo "SUITE FAILED"
exit "$fail"
