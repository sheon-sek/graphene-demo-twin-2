# Performance: measured, optimization deferred

The PRD (#11) sets four non-functional targets. Issue #28 asked for them to be verified. By
decision, #28 **measures and records** them and does not tune anything. Where a target is
missed, the gap is recorded below as debt for a follow-up issue.

Reproduce with:

```sh
.venv/bin/python tools/benchmark.py            # add --quick for fewer samples, --json for raw data
cd apps/web && pnpm build && pnpm exec playwright test -g "benchmark mode"   # console frame rate
```

## Results (2026-09-24, #28)

Measured on the development workstation: Python 3.12.14, x86_64 Linux, one process, no other load.
The world was built from its steady state at sim time 1,790,000,000.

| Measure | mean ms | p95 ms | max ms | n | Target | Verdict |
|---|---|---|---|---|---|---|
| Live World step, steady | 0.6 | 0.7 | 0.8 | 300 | 1 s step | **met**, with ample headroom |
| Live World step, during the Golden Demo incident | 0.6 | 0.7 | 0.7 | 300 | 1 s step | **met** |
| Twin tick: step + project all 8,741 points + publish a frame | 4.7 | 4.9 | 5.1 | 300 | 1 Hz publish | **met** |
| SSE delta encode, per frame | 3.9 | 4.0 | 4.3 | 300 | 1 Hz publish | **met** |
| OPC UA write of one frame (every changed value) | 15.7 | 16.3 | 16.4 | 30 | 1 Hz publish | **met** |

In total, a Live World second costs about 25 ms of the 1,000 ms budget: stepping, projection,
SSE and OPC UA together. The existing tests `test_projecting_a_step_is_cheap` (< 50 ms per
projection) and `test_a_60_minute_fork_runs_in_under_3_s_and_leaves_the_live_world_untouched`
(one fork stepped 3,600 s in < 3 s) guard these numbers.

| 60-minute Fault Preview, end to end | seconds | affected assets | point diffs | Target | Verdict |
|---|---|---|---|---|---|
| `crac.fan_failure` on CRAC/L1_CRAC3 | 5.83 | 404 | 2,034 | < 3 s | **missed** |
| `tower.fan_failure` on Cooling Towers Plant/R_P1_CT1 | 5.83 | 404 | 1,160 | < 3 s | **missed** |
| `utility.incomer_loss` on Meter/SPPA Incomer 1 | 5.82 | 404 | 1,741 | < 3 s | **missed** |
| `chiller.trip` on Chiller/R_C1 | 5.82 | 429 | 2,273 | < 3 s | **missed** |

| Console frame rate, every asset and layer on screen | median fps | p5 fps | Target | Verdict |
|---|---|---|---|---|
| Headless Chromium, SwiftShader (software WebGL), 1600×900 | 21.5 | 11.3 | ≥ 50 fps | **not measured on target hardware** |

## Deferred debt

1. **Fault Preview is about 5.8 s for 60 minutes, against a 3 s target.** A preview steps two
   worlds, the baseline and the faulted fork, for 3,600 s each (about 2.2 s each at 0.6 ms
   per step). Every second it also compares the AssetState of every node not yet affected,
   and every alarm bit not yet changed, so that it can report when each first changed.
   Candidate fixes for the follow-up are: sharing the baseline between previews taken from the
   same Live World second, comparing alarm bits only for nodes already affected, and stepping
   the two forks in parallel. The
   regression bound in `tests/test_faults.py::test_a_60_minute_preview_stays_within_its_measured_bound`
   is set to the measured value plus margin (8 s) until then.
2. **The console's 50 fps target has not been measured on a GPU.** CI and this workstation
   run headless Chromium with software WebGL, which reported 21.5 fps median. The e2e suite
   guards the draw-call budget that keeps 50 fps reachable (`DRAW_CALL_BUDGET` in
   `apps/web/e2e/console.spec.ts`). The frame rate itself still has to be read by hand with
   `?bench` on a mid-range laptop with a GPU.
