# 3D Validation Notes

## Checks implemented

- Scene placement is deterministic and independent of input array order.
- Major system families map to explicit scene areas and layers.
- Connection lines are built only from authoritative `serves`, `feeds`, and `networkParent` topology relations.
- Runtime fault sources are supplied by `/api/stream` snapshot state and are visualized without frontend fault inference.
- Selected asset inspection reports the same topology object returned by the backend.
- The scene does not render point-level Graphene telemetry markers by default.

## Automated coverage

`apps/web/src/__tests__/scene.test.ts` covers semantic mapping, deterministic placement, and topology-link filtering.

## Browser and CI verification

GitHub Actions performs the dependency-resolved frontend unit tests, production build, Chromium installation, and Playwright browser smoke against the real FastAPI service. The browser test exercises the Virtual World route, canvas visibility, view presets, layer state, and captures overview/cooling frames for visual inspection.

The first real browser capture exposed excessive scene cost from facility-scale rendering of 192 Network Switch Port objects. Port telemetry remains authoritative in topology/inspection, but ports are excluded from the equipment scene. Fault visualization is also static and the renderer remains in demand mode so a fault does not force perpetual frame rendering.

Build success alone is not treated as visual approval; captured frames are reviewed separately for composition, object identity, clipping, and readability.
