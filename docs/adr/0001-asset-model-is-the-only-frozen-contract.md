# The Asset Model is the only frozen contract

The v2 rebuild keeps exactly one thing from v1: the Asset Model exported from our Ignition tag structure (`reference/graphene/*.json`). That means 831 UDT instances and 8,741 points, with their export paths, member names, typeIds and data types. A contract test enforces it. Everything v1 derived or invented on top of the export is not a contract and was discarded: the parser, the generated manifests, the signal registry, the inferred topology relations and the 1,022 Twin Extension paths. Ignition only binds to Asset Model points, so preserving anything else would have locked in v1's modelling mistakes (for example, all 64 T&H sensors collapsed into one "Hall-A") without protecting any consumer.

## Consequences

- Twin Extensions are re-derived from what v2's physics and diagnosis need, under `Twin Extensions/`, and carry no compatibility promise.
- The OPC UA surface (namespace `urn:eetarp:graphene:demo:twin`, NodeId `point:<encodedExportPath>`, SourceTimestamp = sim time, read-only) is part of the external contract alongside the Asset Model. Reserved characters in NodeId suffixes are encoded as documented by ADR-0005; BrowseNames and folder paths preserve the exact Asset Model export paths.
