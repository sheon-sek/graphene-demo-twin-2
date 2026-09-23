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
- Asset colours come from `assetStatus` (`src/lib/status.ts`). They are a first-pass reading of an asset's points: bad quality, alarm bits, warning bits, uncertain quality, and run feedback. Whether a point is an alarm bit comes from the point metadata (`alarmBit` in `/api/coverage/points`). A bit is active when it is true or nonzero, so integer bits such as a breaker `Trip` count. Alarm counts, codes and texts are not bits.
- A point belongs to its nearest ancestor that is an Asset, or a Plant View folder that observes an Unexported Asset. The inspector and the tree share this rule (`src/lib/ownership.ts`).

## Faults and Operator Commands

- The inspector's Faults tab lists the catalog for the selected asset's type. The operator sets severity, onset (step or ramp) and duration (until cleared or auto-clear), previews the fault for 15, 30 or 60 minutes in a What-if Fork (`POST /api/faults/preview`), and injects it on exactly that asset. The preview shows the affected nodes in propagation order, the alarm bits that would change and the point diffs at the end of the window.
- The Control tab gives the asset's Operator Commands (hand/auto, start/stop, setpoints). Each one becomes an Event Log entry.
- The bottom bar lists the active faults, each with its own Clear, the alarm bits that are set, the Event Log timeline, and Reset, which asks for confirmation in the page.
- A faulted asset carries a pulsing red ring and beacon, apart from its state colour. The connections downstream of it along the Plant Design (its causal path) are drawn red, and that takes priority over the selection's upstream and downstream lighting.

## Drawing conventions

- Floors stack one storey (5 m) apart. Exploding the stack adds 14 m above each floor. Framing a floor, room or asset cuts away the floors above it.
- Assets sit exactly where the Plant Design places them. Where two share a spot, clicking the selected asset again selects the one behind it. A double-click always flies to the asset directly under the pointer.
- Connections run orthogonally at a per-layer ceiling-tray height. They change floor only in the Plant Design's service shaft for their connection kind (`shafts` in `/api/plant-design`).
- Layers: Electrical (power and fuel), Hydraulic (chilled and condenser water), Airside, Network and Water.
- Each UDT type maps to one procedural silhouette family (`src/lib/silhouettes.ts`). Each family is drawn as one instanced mesh, plus one more for Unexported Assets, which are drawn as translucent wireframe ghosts.

`window.__twin` exposes the frame rate, draw calls, each asset's on-screen position and `locate()`, for the smoke test. `?e2e` makes camera moves instant.

## Frame-rate benchmark

The acceptance target is at least 50 fps with every asset and layer on screen on a mid-range laptop. CI can't measure that: its software renderer is far slower than any laptop GPU. So CI checks only the draw-call budget, and the frame rate is measured by hand:

1. Build the console (`pnpm build`) and start `graphene-twin serve` from the repo root.
2. On the laptop, open `http://127.0.0.1:8080/?bench` in Chrome (8080 is the default `--http-port`). Keep the window focused, visible and at its usual size. Plug the laptop in.
3. The console shows the whole site with every layer on. It warms up for 5 s, then records every frame for 30 s. Change these with `&warmup=` and `&window=` (seconds).
4. The box at the top right reports the median frame rate and the 5th percentile (p5). p5 is the rate of the slowest 5 % of frames. The result is also logged to the browser console as `bench: {…}`.

The target is met when p5 is at least 50 fps. Browsers cap frames at the display refresh rate, so on a 60 Hz screen neither number goes above about 60.
