# Architecture

The engine has one deterministic domain model and one projection path. The real Graphene exports are parsed into a generated compatibility manifest; physical faults are translated into equipment constraints before the world is solved; the solver produces authoritative asset states and network balances; Graphene points are then projected from that world. Raw developer overrides remain a publication-layer tool and do not feed back into ground truth.

```text
Reference exports -> Schema Generator -> Coverage Manifest / Topology / Signal Registry
                                      -> Runtime fault activations
                                      -> Physical constraints
                                      -> DomainModel(timestamp, seed, profile)
                                      -> PhysicalWorldSolver
                                           -> AssetState
                                           -> NetworkBalance
                                      -> BaseWorldSnapshot
                                      -> Non-physical fault/quality projection
                                      -> Graphene Projector
                                      -> Raw Override (developer only)
                                      -> REST/SSE + OPC UA + React Admin
```

## Physical world rules

- Asset state is solved before point projection. Graphene points do not independently invent equipment physics.
- Physical propagation follows explicit topology relations such as `serves` / `servedBy`; the solver does not create proximity links.
- The initial closed-loop slice covers Chiller, Cooling Tower, Chiller Pump and CRAC behavior, including cooling demand, condenser-water effects, chilled-water delivery and cooling-plant electrical balance.
- A physical fault changes solver constraints. For example, a cooling-tower failure reduces tower availability before the linked chiller is solved, so tower power/flow, condenser-water temperature, chiller COP/input power and facility balance change coherently.
- Non-physical concerns such as communication quality and alarm presentation remain in the shared Fault Engine after the physical solve.
- Equipment domains that have not yet migrated to `PhysicalWorldSolver` continue to use deterministic compatibility sources; they are not claimed to have full physical causality yet.

The backend is a single deployable FastAPI service. OPC UA is an adapter, not a physics engine. The frontend reads REST for authoritative initial state and SSE for live telemetry.

## Cooling control and staging

The cooling solver treats the real Graphene Chiller System Control setpoints as authoritative control inputs for the migrated cooling slice. The configured CHWS setpoint drives both Chiller and CRAC chilled-water supply state, while minimum/maximum Chillers and the Chiller Load Limit determine the required staging count and per-Chiller capacity. The configured maximum remains distinct from the number of Chiller assets currently present in topology; staging is always capped by physical availability. Aggregate Graphene control points such as Plant Load, Required Chillers and Running Chillers are projected from this solved control state.

Running-hours lead rotation remains compatibility metadata until authoritative operating-hours integration and an explicit mapping between control-channel names and physical Chiller assets are modeled.

## PAHU demand aggregation

The migrated airside slice now solves each real PAHU asset before cooling-plant staging. Site zone heat is distributed deterministically across the 15 PAHUs, each PAHU derives one shared CHW demand/flow and command-feedback state, and the aggregate PAHU CHW demand becomes the cooling demand consumed by Chiller staging. PAHUs are admitted to this aggregate only through explicit Chiller `serves` / PAHU `servedBy` topology; no equipment-name relationship inference is used.

The real PAHU SAT/RAT/RH/setpoint/run-state surface and the existing diagnostic extensions for static pressure, fan command/feedback, CHW valve command/feedback and CHW flow all project from the same PAHU `AssetState`. The commissioning static-pressure setpoint remains 600 Pa versus the 450 Pa design reference.

`PAHU_AFTER_HOURS` is now a physical pre-solve constraint. It raises the selected PAHU's unnecessary after-hours airflow/CHW demand, which propagates through aggregate CHW demand into Chiller load, Cooling Tower power and facility load/PUE. Because indirect physical constraints can change Cooling Tower power, authoritative Cooling Tower Energy integration now incorporates all physical-constraint history, not only faults directly targeting a Cooling Tower.

CRAC remains the existing migrated cooling-delivery gate in this increment; a later airside refinement can separate CRAC/PAHU zone allocation without inventing a split not present in the current source data.
