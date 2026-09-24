# Graphene Demo Twin

A deterministic virtual datacenter that simulates equipment, physical networks, control and faults as one causal world, then projects that world onto the exact tag structure of our Ignition/Graphene estate. Operators observe and drive it through a 3D console; Ignition and Graphene consume it over OPC UA.

## Language

### Contract with Ignition

**Asset Model**:
The assets and points exported from our Ignition tag structure (instances, UDT types, export paths, member names, data types). It is a frozen external contract: the twin must reproduce it exactly and never rename, reparent or drop any of it.
_Avoid_: schema, tag list, asset list

**Asset**:
One root UDT instance in the Asset Model, identified by its export path.
_Avoid_: device, equipment instance

**Point**:
One atomic tag at an export path; the unit consumers read.
_Avoid_: tag, signal, datapoint

**Twin Extension**:
A point the twin adds under `Twin Extensions/` because physics or diagnosis needs it; it is not part of the Asset Model and carries no compatibility promise.
_Avoid_: extra tag, diagnostic extension

**Plant View**:
An export folder that observes physical assets already modelled elsewhere rather than adding equipment of its own (`Chiller System Control` is the supervisory view, `Chiller_System` the instrument view, `Dashboard` the KPI view). Its points are projections of those assets.
_Avoid_: duplicate plant, second chiller system

**Unexported Asset**:
A physical asset in the Plant Design that has no UDT instance in the Asset Model and is observed only through Plant View points (for example the fourth chiller, CH-004).
_Avoid_: phantom asset, virtual chiller

**Support Asset**:
An asset in support scope (Smart Alarm Logic, Dashboard, PredictionCache, MQTT Tags, Testing) whose points are projected but which is not a physical thing in the world.
_Avoid_: virtual asset, helper tag

### The site

**Data Hall**:
One of the eight IT rooms DH01–DH08 (DH01–04 on Level 1, DH05–08 on Level 2); each is its own thermal zone.
_Avoid_: Hall-A, Hall-B, datahall, room

**Thermal Zone**:
A room whose air the simulation integrates as one volume with thermal inertia: every Data Hall, and every support room that has airside units. Heat comes in from what dissipates in the room and leaves through the cooling its air suppliers deliver; zones share heat only along authored air connections.
_Avoid_: hall model, room loop

**Cooling Block**:
The chilled-water branch serving one Data Hall (CB-001…CB-008), with its own valves, flow meter and supply/return temperatures.
_Avoid_: hall loop, CHW zone

**Tower Group**:
Five cooling-tower cells that together reject heat for one chiller's condenser (CT-001…CT-004).
_Avoid_: tower bank, cell set

**IT Load**:
The electrical power drawn by IT equipment in one Data Hall; all of it becomes heat in that hall.
_Avoid_: rack load, server load

**Fire Zone**:
One zone of the fire alarm panel on one floor (`Fire Protection System/<floor>/<zone>`), covering one or more rooms. A zone in alarm shuts down the fresh-air handlers supplying its rooms and recalls the lifts, along its authored `fire` connections.
_Avoid_: fire area, detection loop

**Demo Rack**:
The standalone demonstration cabinet exported as `DemoRack/`, with its own small electrical system that is not connected to the site.
_Avoid_: test rack, lab rack

### The simulated world

**Plant Design**:
The hand-authored physical design of the site: how every asset is connected (electrical, hydraulic, airside, network, water) and where it sits in space. It is authored and reviewed, never inferred from names.
_Avoid_: topology (ambiguous), inferred relations

**AssetState**:
The single authoritative physical state of one asset at one instant; the only source of truth for that asset's points.
_Avoid_: snapshot, metrics

**Event Log**:
The ordered, sim-timestamped record of every operator action (fault inject/clear, Operator Commands). Together with the seed and the initial state it fully determines the world's trajectory.
_Avoid_: history, audit log

**Live World**:
The one simulation that runs locked to wall-clock time at 1x and is exposed over OPC UA. It is never paused, accelerated or rewound.
_Avoid_: production world, main sim

**What-if Fork**:
A copy of the Live World's state advanced independently (paused, accelerated, with hypothetical events) for preview or replay; nothing outside the Operator Console ever observes it.
_Avoid_: shadow world, sandbox, simulation copy

**Controller**:
Simulated BMS/PLC logic that reads the world and drives command points (staging, PID loops, ATS transfer, refill); feedback points report how equipment actually responded.
_Avoid_: automation, control logic

**Operator Command**:
A manual action taken in the Operator Console on one asset (hand/auto, start/stop, setpoint change), recorded in the Event Log.
_Avoid_: override, manual write

**Base World**:
The trajectory the world follows from its initial state under the Plant Design and seed when the Event Log contains no faults.
_Avoid_: baseline, ground truth

**Coherent World**:
A single simulation state shared by every surface (REST, SSE, OPC UA, Operator Console); all surfaces observe the same world at the same sim time.
_Avoid_: single source of truth (too generic)

**Staging**:
The selection of which chillers run for a given cooling demand.
_Avoid_: scheduling, rotation

**Delivered Fraction**:
The fraction of zone cooling demand actually delivered by the airside to the zone.
_Avoid_: delivery ratio

### Faults

**Fault**:
A named failure mechanism bound to an asset type, in one of five categories: equipment, sensor, communication, control, or external. It acts only through Physical Constraints, observation corruption (sensor faults), quality changes (communication faults), or Controller misbehaviour, and never writes alarm points directly.
_Avoid_: failure, incident, error

**Physical Constraint**:
A fault's effect on the world expressed as a physical quantity (availability, valve position, degradation) that the simulation consumes, rather than a direct write to any point.
_Avoid_: fault effect, point patch

**Fault Preview**:
The predicted affected assets, point changes and alarms obtained by running a proposed fault in a What-if Fork.
_Avoid_: impact estimate, expected impact

**Clear**:
Removing one fault's Physical Constraint; the world then recovers through its own dynamics rather than snapping back.
_Avoid_: undo, restore

**Reset**:
Discarding the Event Log and rebuilding the Live World from its steady-state initial state; indistinguishable from a restart.
_Avoid_: clear all, restart

**Open World**:
A mode in which operator-injected faults are applied on top of the Base World.
_Avoid_: free play, sandbox

**Golden Demo**:
A pre-authored Event Log played against the Live World.
_Avoid_: demo scenario, script

### Observation

**Projection**:
The mapping of world state onto point paths, producing one typed value per point.
_Avoid_: export, publish

**Raw Override**:
A diagnostic point-level correction applied after projection; it never feeds back into physics.
_Avoid_: override fault, manual value

**Evidence Surface**:
The set of points an AI can use to diagnose one asset: command, feedback, process values, state, faults, quality, upstream, downstream, and peer evidence.
_Avoid_: evidence, diagnostics

**Setpoint Mirror**:
A point that reflects a control setpoint rather than a measured or computed physical value.
_Avoid_: shared setpoint

**Causal Dead End**:
A point whose value cannot respond to any physical state change: a constant, a setpoint mirror, or a compatibility fallback.
_Avoid_: dead point, static tag

**Compatibility Fallback**:
A deterministic, non-physical value for a point outside the modeled world. Permitted only for static-metadata and test-support points; every production point must have a causal source.
_Avoid_: dummy value, placeholder

### Surfaces

**Operator Console**:
The web app the demo operator uses to observe and drive the simulated world, with the 3D scene as its main canvas. It does not replace the Ignition HMI.
_Avoid_: admin UI, dashboard (Dashboard is an asset type)
