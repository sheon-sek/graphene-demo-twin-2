# Verification Evidence

Local verification performed in the build environment:

- `PYTHONPATH=src pytest -q` → **16 passed**.
- Live Uvicorn bind on `127.0.0.1:8099` → `/api/status` HTTP 200.
- `/api/schema/coverage` → 47 UDT types, 831 UDT instances, 296 folders, 8,741 exported AtomicTags, 1,019 additive diagnostic extensions, 9,760 total projected points.
- CLI `status` against the live server returned the same authoritative runtime state.
- Python source passes `compileall`.
- Frontend dependency installation could not be executed in this build environment because the npm registry DNS request returned `EAI_AGAIN`. React/Vite/R3F source and Vitest/Playwright test sources are included, but production frontend build is not claimed as locally verified.
- `asyncua` was not preinstalled and external package retrieval was unavailable; the OPC UA adapter is implemented but live client/server verification is not claimed in this environment.

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
