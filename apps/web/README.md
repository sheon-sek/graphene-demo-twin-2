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

- `/api/plant-design`, `/api/assets`, `/api/coverage/points` and `/api/faults/catalog` are loaded once into a `World` (`src/lib/world.ts`). The `World` holds the layout, the instanced scene model, the Asset Model tree, the search index and the fault catalog.
- `/api/stream` feeds a `LiveStore` (`src/lib/live.ts`). The store keeps the latest reading of every point, a 120-sample history for sparklines, and the active faults, which every frame carries in full. A new snapshot, which the server sends after a Reset or a reconnect, replaces everything.
- The Event Log timeline refetches `/api/events` whenever a frame reports a new Event Log length or epoch. The Control tab refetches `/api/commands/{path}` for the same reason.
- Asset colours come from `assetStatus` (`src/lib/status.ts`). They are a first-pass reading of an asset's points: bad quality, alarm bits, warning bits, uncertain quality, and run feedback.

## Faults and Operator Commands

- The inspector's Faults tab lists the catalog for the selected asset's type. The operator sets severity, onset (step or ramp) and duration (until cleared or auto-clear), previews the fault for 15, 30 or 60 minutes in a What-if Fork (`POST /api/faults/preview`), and injects it on exactly that asset. The preview shows the affected nodes in propagation order, the alarm bits that would change and the point diffs at the end of the window.
- The Control tab gives the asset's Operator Commands (hand/auto, start/stop, setpoints). Each one becomes an Event Log entry.
- The bottom bar lists the active faults, each with its own Clear, the alarm bits that are set, the Event Log timeline, and Reset, which asks for confirmation in the page.
- A faulted asset carries a pulsing red ring and beacon, apart from its state colour. The connections downstream of it along the Plant Design (its causal path) are drawn red, and that takes priority over the selection's upstream and downstream lighting.

## Drawing conventions

- Floors stack one storey (5 m) apart. Exploding the stack adds 14 m above each floor. Framing a floor, room or asset cuts away the floors above it.
- Connections run orthogonally at a per-layer ceiling-tray height. They change floor only in one riser per layer, beside the lift core. The Plant Design has no risers, so their position is a drawing convention (`RISERS` in `src/lib/routing.ts`).
- Layers: Electrical (power and fuel), Hydraulic (chilled and condenser water), Airside, Network and Water.
- Each UDT type maps to one procedural silhouette family (`src/lib/silhouettes.ts`). Each family is drawn as one instanced mesh, plus one more for Unexported Assets, which are drawn as translucent wireframe ghosts.

`window.__twin` exposes the frame rate, draw calls, each asset's on-screen position and `locate()`, for the smoke test. `?e2e` makes camera moves instant.
