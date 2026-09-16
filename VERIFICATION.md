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
