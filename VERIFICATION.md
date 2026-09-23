# Verification Evidence

Local verification performed in the build environment:

- Original bootstrap verification: `PYTHONPATH=src pytest -q` → **16 passed**.
- Formal GitHub Actions Verify #16 for the Hall-A thermal slice: backend → **37 passed, 2 warnings**; frontend unit tests and production build → **success**; Playwright visual smoke and capture upload → **success**.
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

## PAHU demand aggregation verification

The PAHU migration adds checks that:

- all 15 real PAHU assets are solved from one airside thermal-demand source and are connected through explicit Chiller `serves` topology;
- the sum of PAHU Cooling Demand equals the authoritative PAHU/network cooling demand;
- the sum of PAHU CHW Flow equals the network PAHU CHW flow balance;
- real SAT/RAT/setpoints and diagnostic static-pressure/fan/valve/CHW-flow paths project from the same PAHU `AssetState`;
- `PAHU_AFTER_HOURS` increases the selected PAHU fan speed and CHW flow and propagates to higher aggregate cooling demand and facility load;
- indirect PAHU physical constraints change the authoritative Cooling Tower Energy slope consistently with the resulting Cooling Tower power.

## Hall / zone thermal verification

The first authoritative Hall thermal slice adds checks that:

- `Hall-A` closes `zone heat = airside cooling delivered + thermal unmet cooling`;
- the Hall heat input is the same authoritative zone-cooling demand used by the cooling/airside network;
- CRAC delivery loss reduces airside cooling, increases thermal unmet cooling and raises Hall-A temperature;
- runtime ground truth exposes the shared `ThermalZoneState`;
- all 64 real `Temperature and Humidity` instances (128 `Temp` / `Humidity` points) observe that shared Hall-A state with bounded deterministic sensor variation;
- identical timestamp/seed/config/topology/constraints continue to reproduce identical thermal state and projected sensor values.

The current topology does not contain an explicit PAHU/CRAC-to-Datahall or PAHU/CRAC-to-`Hall-A` service allocation. Verification therefore intentionally stops at one source-supported aggregate Hall-A state; it does not claim per-Datahall thermal coupling or an authoritative Hall-B model.
