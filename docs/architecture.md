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
