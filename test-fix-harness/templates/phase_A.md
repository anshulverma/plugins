# Phase A - Diagnose (worker prompt template)

You are a worker diagnosing ONE test on a pinned checkout. You CANNOT land, push,
or amend anyone else's commit. Do all reasoning on CPU; do not hold a GPU.

Read ~/.claude/LOCAL.md if present for local names and details. The `TFH_*`
environment variables below hold the local commands.

Inputs (from payload.json): `unit_id`, `test_target`, `test_case`, `category`
(FAILURE|FLAKY|SKIPPING), `gpu_req`, `base_commit`, `prior_attempts`.

Steps:
1. Confirm the checkout is at `base_commit` (`sl id -i`).
2. Reproduce: run the test via `buck2 test <test_target> -- <test_case>`.
   - If `category=FLAKY` (the test platform's authoritative flaky label): run it AT LEAST 20
     times and record `pass_rate` and `flaky`. A small sample misses intermittent
     failures, so do NOT conclude non-flaky from a few passes. Set `flaky=true` if
     any run fails (0 < pass_rate < 1). If all 20 pass, still keep the platform's flaky
     label in mind for downstream de-flaking (note it may be already-stabilized).
   - If `category=FLAKY` and ALL local reruns PASS (not reproducible locally): it
     is likely CI/RE-only flaky. Do NOT return cause `unknown`. Pull the REAL
     failing runs from the test platform and diagnose from those, using its
     history CLI (`$TFH_TEST_HISTORY_CLI`):
       look up the test id by `<test_case or target>`
       list its FAILED results over the last month (JSON)
       fetch each failing run in full                     (real stack/exception)
       fetch that run's logs                              (log artifacts)
     Diagnose from the actual CI failure: set `culprit_symbol`, a real
     `cause_category` (flaky_timing / config / data_dependent / resource_env /
     ...), `error_signature` = the CI error, `evidence_ref` = the failing testrun
     URL, `reproduced=false`, `flaky=true`, `reason_not_reproduced=flaky-timing`.
     Use `unknown` ONLY if even the CI logs are inconclusive.
   - If the test needs a GPU to execute, do NOT block: return `status`
     `NEEDS_GPU_EXEC` with a `gpu_request` object, or `NEEDS_REROUTE` if it needs
     a different lane (e.g. multi-GPU/NCCL detected).
3. Run the debug skill named by `$TFH_DEBUG_SKILL` to diagnose flaky-vs-real and
   find the cause.
4. Determine the root cause(s). For each, set `culprit_symbol` = the
   `file:symbol` where the defect LIVES (distinct from `error_signature`, where
   it manifests), a `signature` = sha1(normalize(culprit_symbol)+'|'+cause_category),
   and `cause_category` = EXACTLY ONE of this fixed enum (no free-form):
   `product_bug` (code logic/assertion/runtime), `test_bug` (defect in the test),
   `type_error` (Pyre/static type), `missing_dependency` (unregistered/missing
   package/target/resource), `build_error` (buck build/config not product logic),
   `config` (feature knob/gate/Hydra/build-mode drift), `flaky_timing` (race/timing/
   concurrency), `resource_env` (needs GPU/network/quota absent in env),
   `intentional_skip` (feature-flag/deprecated/quarantined), `data_dependent`
   (external data changed), `unknown`.

Output: write ONLY a JSON object to the path given as the result file, matching
this schema exactly (no extra keys):

```json
{
  "unit_id": "<from payload>",
  "status": "completed | NEEDS_GPU_EXEC | NEEDS_REROUTE | error",
  "reproduced": true,
  "flaky": false,
  "pass_rate": 0.2,
  "reason_not_reproduced": "env-missing-resource | flaky-timing | gpu-unavailable | config-drift | unknown | null",
  "root_causes": [
    {"culprit_symbol": "path/file.py:func", "cause_category": "logic",
     "mechanism": "…", "error_signature": "AssertionError: …",
     "observed_frequency": 1.0, "signature": "<sha1>"}
  ],
  "evidence_ref": "<durable ref: buck2 UI / testrun URL, or a paste>"
}
```

`reproduced:false` is a valid terminal outcome; still provide a hypothesized
`root_causes` entry and a `reason_not_reproduced`. Never land or push.

For `evidence_ref`, prefer a DURABLE, accessible reference: a buck2 UI / testrun
URL, or create a paste of the build/repro log
(`$TFH_PASTE_CMD --title tfh-<unit_id> < log`) and use the paste id it
returns. If you must keep a local log, write it to `evidence.txt` NEXT TO the
result file (same directory) so it is collected - a bare local path elsewhere is
lost for remote workers. Always keep the key error inline in `error_signature`.
