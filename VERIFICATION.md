# Verification Evidence

Local verification performed in the build environment:

- `PYTHONPATH=src pytest -q` → **16 passed**.
- Live Uvicorn bind on `127.0.0.1:8099` → `/api/status` HTTP 200.
- `/api/schema/coverage` → 47 UDT types, 831 UDT instances, 296 folders, 8,741 exported AtomicTags, 1,019 additive diagnostic extensions, 9,760 total projected points.
- CLI `status` against the live server returned the same authoritative runtime state.
- Python source passes `compileall`.
- Frontend dependency installation could not be executed in the original local build environment because the npm registry DNS request returned `EAI_AGAIN`; formal GitHub Actions now verifies Vitest, the production frontend build and Playwright visual smoke.
- `asyncua` was not preinstalled in the original local build environment; formal GitHub Actions now runs the live asyncua server/client smoke test, including read-only access and simulation SourceTimestamp.

The authoritative Graphene coverage check does not depend on these unavailable external packages.

## Asset-state / network-balance verification

The feature branch adds deterministic physical-world invariants for the cooling-plant slice:

- cooling demand = cooling delivered + unmet cooling;
- site plant load is sourced from the solved cooling-plant electrical balance;
- facility load = IT load + cooling-plant load + non-cooling auxiliary load;
- PUE reconciles to facility load / IT load;
- Chiller Cooling Output = Input Power × COP;
- identical timestamp/seed/constraints produce identical asset state and projected signals;
- a CRAC valve-stuck constraint reduces valve feedback/CHW flow and increases SAT/unmet cooling/hall temperature;
- a Cooling Tower failure drives tower fan power/speed to zero and propagates through explicit `serves` topology into higher linked-chiller CW supply temperature, lower COP, higher chiller input power and higher facility PUE.

These checks are executed by the backend test suite in GitHub Actions. Domains outside the cooling-plant slice remain deterministic compatibility sources and are not claimed here as fully physically coupled.

## Cooling control / staging verification

The migrated cooling control layer is also checked for:

- real Graphene CHWS Temperature SP drives the solved Chiller and CRAC CHW supply state;
- real minimum/maximum Chiller settings and the Chiller Load Limit drive deterministic demand staging;
- configured maximum Chillers remains distinct from currently available topology assets and staging cannot exceed physical availability;
- Chiller load fraction cannot exceed the configured load-limit setpoint;
- Graphene Plant Load, Required Chillers, Running Chillers, Min/Max Chillers, CHWS Temperature SP and Chiller Load Limit are projections of one authoritative cooling-control state.
