# Contracts

- Internal model time is UTC; timestamps must be timezone-aware.
- Same timestamp + seed + config + profile returns the same base snapshot.
- Runtime faults do not mutate base truth.
- Raw overrides are post-projection developer diagnostics.
- Production OPC NodeId is `point:<exportPath>` in namespace `urn:eetarp:graphene:demo:twin`.
- Existing Graphene export paths, type IDs and member names are immutable.
- AI-visible data excludes scenario/runtime-control truth and support/test roots.
