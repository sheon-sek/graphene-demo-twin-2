# Operator Console

The web app the demo operator uses to observe the Live World (see `CONTEXT.md`). It is built with React, React Three Fiber and Vite. `graphene-twin serve` serves the built `dist/` at `/`, so the console, REST, SSE and OPC UA all come from one process.

```sh
pnpm install
pnpm dev        # Vite on :5173, proxying /api to a running `graphene-twin serve` (TWIN_API to change)
pnpm test       # Vitest: the pure modules in src/lib and the panels
pnpm build      # type-check and build dist/
pnpm e2e        # Playwright: builds nothing; starts `serve` on dist/ and drives the console
```

`pnpm e2e` uses `../../.venv/bin/python` when it exists, otherwise `python` (set `TWIN_PYTHON` to override). It writes one capture per floor, and one of the whole site, to `test-results/floors/`.

## What reads what

- `/api/plant-design`, `/api/assets` and `/api/coverage/points` are loaded once into a `World` (`src/lib/world.ts`). The `World` holds the layout, the instanced scene model, the Asset Model tree and the search index.
- `/api/stream` feeds a `LiveStore` (`src/lib/live.ts`). The store keeps the latest reading of every point and a 120-sample history for sparklines. A new snapshot, which the server sends after a Reset or a reconnect, replaces everything.
- Asset colours come from `assetStatus` (`src/lib/status.ts`). They are a first-pass reading of an asset's points: bad quality, alarm bits, warning bits, uncertain quality, and run feedback.

## Drawing conventions

- Floors stack one storey (5 m) apart. Exploding the stack adds 14 m above each floor. Framing a floor, room or asset cuts away the floors above it.
- Connections run orthogonally at a per-layer ceiling-tray height. They change floor only in one riser per layer, beside the lift core. The Plant Design has no risers, so their position is a drawing convention (`RISERS` in `src/lib/routing.ts`).
- Layers: Electrical (power and fuel), Hydraulic (chilled and condenser water), Airside, Network and Water.
- Each UDT type maps to one procedural silhouette family (`src/lib/silhouettes.ts`). Each family is drawn as one instanced mesh, plus one more for Unexported Assets, which are drawn as translucent wireframe ghosts.

`window.__twin` exposes the frame rate, draw calls, each asset's on-screen position and `locate()`, for the smoke test. `?e2e` makes camera moves instant.
