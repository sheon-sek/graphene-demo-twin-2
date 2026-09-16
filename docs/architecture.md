# Architecture

The engine has one deterministic `DomainModel` and one projection path. The real Graphene exports are parsed into a generated compatibility manifest; the domain computes authoritative signals; scripted or injected faults create an effective snapshot; raw developer overrides apply only after Graphene projection.

```text
Reference exports -> Schema Generator -> Coverage Manifest / Topology / Signal Registry
                                      -> DomainModel(timestamp, seed, profile)
                                      -> BaseWorldSnapshot
                                      -> Shared Fault Engine
                                      -> Effective World
                                      -> Graphene Projector
                                      -> Raw Override (developer only)
                                      -> REST/SSE + OPC UA + React Admin
```

The backend is a single deployable FastAPI service. OPC UA is an adapter, not a physics engine. The frontend reads REST for authoritative initial state and SSE for live telemetry.
