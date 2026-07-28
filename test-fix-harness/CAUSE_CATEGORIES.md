# Cause-category taxonomy

Phase-A workers MUST set each root cause's `cause_category` to exactly one value
below (no free-form). Each maps to a `fix_class` used by metrics and to decide
whether it becomes a code diff, an infra task, or a won't-fix.

| cause_category | fix_class | description |
|---|---|---|
| `product_bug` | code | Defect in the code under test: wrong logic, bad assertion hit, runtime exception from product code. Becomes a code diff. |
| `test_bug` | code | Defect in the test itself: wrong expectation, stale golden, bad fixture/mock, over-strict assertion. Code diff to the test. |
| `type_error` | code | Static type / Pyre error in product or test code. Code diff (annotation / guard). |
| `missing_dependency` | infra | Unregistered or missing fbpkg / build target / resource / dataset the test needs (e.g. bot-generated fbpkg never created). Infra task, NOT a code diff. |
| `build_error` | infra | Buck build/config failure unrelated to product logic (target won't load/compile, bad BUCK wiring). Usually infra/config. |
| `config` | infra | Config / knob / GK / JustKnob / Hydra / build-mode mismatch or drift. |
| `flaky_timing` | flaky | Nondeterministic failure: race condition, timing, concurrency, order-dependence. Characterize with pass_rate. |
| `resource_env` | infra | Needs a resource absent in the run env (GPU/NCCL/network/quota) -> capability skip, not a real failure. |
| `intentional_skip` | wontfix | Deliberately skipped/disabled/gated (feature flag off, deprecated, quarantined). Do not fix; recommend cleanup. |
| `data_dependent` | infra | Depends on external data that changed/moved (Hive/Manifold path, snapshot). |
| `unknown` | unknown | Could not determine a cause with the available evidence. |

`fix_class` rollup: **code** (product_bug, test_bug, type_error) = candidate diffs;
**infra** (missing_dependency, build_error, config, resource_env, data_dependent)
= infra tasks / not code diffs; **flaky** (flaky_timing) = stabilize/quarantine;
**wontfix** (intentional_skip); **unknown**.
