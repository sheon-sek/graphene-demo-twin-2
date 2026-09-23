# Graphene Demo Twin

A deterministic digital twin of a datacenter cooling/electrical/network estate, built from a real Graphene/Ignition export. Faults are injected into a shared world and observed through REST/SSE, OPC UA, and a 3D console.

## Language

**Base World**:
The deterministic, fault-free reference state of the estate, derived from the real export and a fixed seed.
_Avoid_: baseline, ground truth

**AssetState**:
The single authoritative physical state of one asset at one instant; the only source of truth for that asset's signals.
_Avoid_: snapshot, metrics

**Physical Constraint**:
A fault's effect on the world expressed as a physical quantity (availability, valve position, degradation) that the solver consumes, rather than a direct write to any point.
_Avoid_: fault effect, point patch

**Projection**:
The mapping of world state onto exported point paths, producing one typed value per point.
_Avoid_: export, publish

**Raw Override**:
A diagnostic point-level correction applied after projection; it never feeds back into physics.
_Avoid_: override fault, manual value

**Evidence Surface**:
The set of points an AI can use to diagnose one asset: command, feedback, process values, state, faults, quality, upstream, downstream, and peer evidence.
_Avoid_: evidence, diagnostics

**Causal Dead End**:
A point whose value cannot respond to any physical state change: a constant, a setpoint mirror, or a compatibility fallback.
_Avoid_: dead point, static tag

**Coherent World**:
A single simulation state shared by every surface (REST, SSE, OPC UA, UI); all surfaces observe the same world.
_Avoid_: single source of truth (too generic)

**Staging**:
The selection of which chillers run for a given cooling demand.
_Avoid_: scheduling, rotation

**Delivered Fraction**:
The fraction of zone cooling demand actually delivered by the airside to the zone.
_Avoid_: delivery ratio

**Setpoint Mirror**:
A point that reflects a control setpoint rather than a measured or computed physical value.
_Avoid_: shared setpoint

**Compatibility Fallback**:
A deterministic, non-physical value assigned to points outside the modeled physics so no point is ever missing.
_Avoid_: dummy value, placeholder

**Golden Demo**:
The scripted fault timeline played in demo mode.
_Avoid_: demo scenario, script

**Open World**:
A mode in which operator-injected faults are applied on top of the Base World.
_Avoid_: free play, sandbox
