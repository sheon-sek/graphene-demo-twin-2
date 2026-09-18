# Implementation Plan / State

1. Graphene export audit — VERIFIED by parser inventory and coverage tests.
2. Topology + diagnostic gap generation — IMPLEMENTED.
3. Deterministic domain foundation — IMPLEMENTED and tested.
4. Shared fault overlay — IMPLEMENTED for required catalog recipes.
5. Graphene projection + raw override — IMPLEMENTED.
6. Runtime lifecycle + REST/SSE — IMPLEMENTED and API tested.
7. OPC UA adapter — IMPLEMENTED; GitHub CI runs a real asyncua server/client read-only + SourceTimestamp smoke test.
8. React/Three.js admin — IMPLEMENTED; GitHub CI validates unit tests, production build and Playwright visual smoke.
9. Full high-fidelity physics and per-system calibration — IN_PROGRESS; cooling-plant AssetState/NetworkBalance, authoritative Cooling Tower Power→Energy, and Chiller control/staging are migrated first.
