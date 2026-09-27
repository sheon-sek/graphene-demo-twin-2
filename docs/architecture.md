# Architecture

The twin is one Python process. It holds one causal world and projects that world onto the
Ignition Asset Model, then serves it over REST/SSE, OPC UA and the Operator Console. The
glossary is in `CONTEXT.md`, and the decisions behind this design are in `docs/adr/`.

```
 reference/graphene/*.json      plant-design/plant-design.json
   (Asset Model, frozen)          (Plant Design, authored)
            │                              │
            ▼                              ▼
   asset_model.load_asset_model   plant_design.load_plant_design
            │                              │
            │        ┌─────────────────────┘
            │        ▼
            │   sim.Simulation ── domains, stepped in order each 1 s ──┐
            │     │  (WorldState: one AssetState per node)             │
            │     │  Event Log: fault.inject / fault.clear / command   │
            │     ├── sim.LiveWorld   (wall clock, 1x, never rewound)  │
            │     └── WhatIfFork      (paused, accelerated, previews)  │
            ▼                                                          │
   projection.Projector ◄──────────────────────────────────────────────┘
     (8,811 points: value + quality, typed to the Ignition data type)
            │
            ▼
   twin.Twin ── publishes one Frame per Live World second (the Coherent World)
      │            │                    │
      ▼            ▼                    ▼
  surfaces.api  surfaces.api /stream  surfaces.opcua
  REST + console   SSE (1 Hz)         OPC UA (1 Hz, SourceTimestamp = sim time)
```

## Layers

**Asset Model** (`asset_model/`). Loads the frozen export and exposes every Asset and Point
with its export path, typeId, data type and export value. It also judges each point's
*source class* (`classify.py`): command, feedback, process value, equipment state,
fault/alarm, energy integral, network state, static metadata or support. A contract test
pins the export (ADR-0001).

**Plant Design** (`plant_design/`, `plant-design/`). The hand-authored physical design:
floors, rooms, shafts, every asset's position and its connections by kind (power, CHW, CW,
air, water, fuel, fire, net). It is authored, never inferred (ADR-0002). Propagation happens
only along these connections, and the console draws only these positions and routes.

**Simulation** (`sim/`, `faults/`). A stateful, fixed-step world (ADR-0003). `WorldState`
holds one `AssetState` (named scalars) per Plant Design node. Each domain is a stateless step
function over that state, run in the order that `world.default_domains` gives:

1. `FaultDomain`: turns each active fault into its mechanism variable (`constraint.*`,
   `observation.*`, `quality.*`, `controller.*`) at its current level (onset ramp,
   auto-clear).
2. `NetworkDomain`: device health, reachability and communication quality.
3. `WeatherDomain`: tropical weather, and the wet bulb that drives the towers.
4. `FireDomain` and `LiftDomain`: fire zones, which stop fresh-air handlers and recall lifts.
5. `ITLoadDomain`: IT Load per Data Hall, all of which becomes heat in that hall.
6. `CracDomain`, `ChillerPlantDomain` and `ChilledWaterUnitDomain`: DX units, the chiller
   plant with its Controllers (staging, rotation, DP and bypass PID, tower fans, buffer tanks)
   and the chilled-water air units.
7. `WaterDomain`: tanks, transfer, booster and makeup pumps, the closed-loop makeup, and
   leaks.
8. `SiteLoadDomain` and `ElectricalDomain`: loads, power flow, UPS, gensets, ATS and fuel.
9. `ThermalZoneDomain` and `ZoneSensorDomain`: the room heat balance, and the sensors that
   observe it.
10. `SitePowerDomain`: site totals and KPIs.
11. `DemoRackDomain`: the standalone Demo Rack, which shares nothing with the site.

A domain that carries out **Operator Commands** declares them per asset type (`CommandSpec`).
Chillers, CRAC units, tower cells, chilled-water air units and the water pumps carry a
hand/auto selector (most share `sim.commands.HAND_AUTO`). They follow their Controller in
auto and the operator's start/stop in hand. Gensets have auto/manual, a manual run switch
and an emergency stop. Buffer tanks have hand/auto on their valves and a bypass switch.

The **Event Log** (fault inject/clear and Operator Commands, sim-timestamped) and the seed
fully determine the trajectory. The **Live World** is the only world the surfaces see. It
steps on every wall-clock second and is never paused or rewound. **What-if Forks** copy its
state for Fault Preview and replay. **Reset** rebuilds the world from its steady-state
initial state.

**Faults** (`faults/catalog.py`, reference in `docs/fault-catalog.md`). Each fault is a
mechanism bound to an asset type in one of five categories. It acts only through its
variable, and never writes an alarm point: alarm bits are device logic reading state. A
**Fault Preview** runs the fault in a fork beside an untouched baseline fork, and reports the
affected nodes in propagation order, the point diffs and when each alarm bit first changed.

**Projection** (`projection/`). Bindings map world state onto points: a `Binding` (one
point), a `GroupBinding` (many points from one expensive derivation, such as a power-flow
pass over every meter) or a `QualityBinding` (communication quality over a set of points).
Values are coerced to the Ignition data type, so every surface shows identical numbers.
Plant Views (`Chiller System Control`, `Chiller_System`, `Dashboard`) are projections of
assets modelled elsewhere, including Unexported Assets such as CH-004 (ADR-0004).

**Twin and surfaces** (`twin.py`, `surfaces/`, `serve.py`). `Twin` owns the Live World and
publishes a `Frame` (projection, a copy of the state, and the Event Log) for every second it
steps. REST, SSE and OPC UA read only published frames, so they always agree on the sim
second (the Coherent World). One `graphene-twin serve` process runs all of them; see
`docs/cli.md`.

**Operator Console** (`apps/web/`). React + React Three Fiber. It shows the 3D building from
the Plant Design, the Asset Model tree, an inspector (Points, Physics, Control and Faults
with Fault Preview), and a bottom bar with active faults, alarms, the Event Log, Golden Demo
and Event Log controls, and Reset. See `apps/web/README.md`.

## The Golden Demo and Event Log documents

An Event Log document (`event_log.py`, format `graphene-demo-twin/event-log` v1) holds
operator actions at offsets in seconds. `GET /api/events/export` writes the Live World's
Event Log as one, with offsets counted from the last time the world was built.
`POST /api/events/import` plays a document against the Live World: by default it resets the
world, then logs each action at its offset from now. Every action, and the story as a whole
(no fault injected twice, none cleared while inactive), is checked before any is logged.

The **Golden Demo** (`src/graphene_demo_twin/golden_demo.json`) is a pre-authored document of
this kind. It tells a multi-domain story over 20 minutes:

1. The wet bulb rises.
2. Utility supply to side A fails. Gensets 1–3 start, and the ATS transfers MSB A within
   15 s while the side-A UPS bridge on battery.
3. On genset power, UPS 1's rectifier fails, and that one UPS goes on battery and drains.
4. A fan in CH-001's Tower Group fails under the high wet bulb, and the condenser water warms.
5. The grid returns and the ATS retransfers.
6. Everything clears, and the world recovers through its own dynamics.

`tests/test_golden_demo.py` checks each step of the story against the physics and the
points. The console plays it from the bottom bar.

## The zero-fallback audit

`audit.py` fails CI (through `tests/test_audit.py`) if any production point is a Causal Dead
End. A production point is any point outside a Support Asset that is not static metadata.
A dead end is one of three kinds:

- a **Compatibility Fallback**: nothing binds it;
- a **constant**: its binding reads no world state (found by evaluating each binding against
  a probe that records every AssetState variable read). A group binding drives many points
  from one read, so each of its points is checked on its own: the audit perturbs every
  variable the group reads, one at a time, and a point that no perturbation moves is a
  constant. A text variable is tried with every value the reader compares it against;
- a **Setpoint Mirror**: a measured, computed or alarm point that only setpoints move.

The CI gate audits two states: the steady initial state, and a stressed one (the utility
lost, every chiller and tower cell run in hand), so a point that only moves away from steady
state is still seen to move.

No production point is exempt. A switch port that the Plant Design leaves unconnected is
admin down and empty, but an Operator Command on its switch's `Network Switches/` view
(`patch` / `unpatch` with the port number) plugs a temporary PoE access point into it. Its
link, speed, PoE draw and utilization then follow the switch, and each drop of the link
counts one error. Configuration constants set at commissioning, such as the staging strategy
or the rotation schedule, are classified as static metadata in `asset_model/classify.py`.

## Performance

Measured, not tuned; see `docs/performance.md`. A Live World second costs about 25 ms of
its 1 s budget. The 60-minute Fault Preview is about 5.8 s against a 3 s target, and the
console frame rate has yet to be measured on a GPU. Both are recorded there as deferred debt.
