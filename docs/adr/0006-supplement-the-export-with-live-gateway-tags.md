# Supplement the export with tags the live gateway has

Status: accepted
Date: 2026-09-28

## Context

The live `[DemoTwin]` tag provider has grown past the Ignition export in `reference/graphene/`
(ADR-0001): `DemoRack/RCMS425`, a PUE per Data Hall under `Environment Monitoring`, and a
set of Dashboard KPIs (`Dashboard/Data Halls`, `Dashboard/Carbon Footprint`, `Facility Load`,
`IT Load`, `PUE (New)`, `TSE`, and others). Ignition binds those tags to `Graphene Demo Twin`,
but the twin had no node for them, so they read `BadNodeIdUnknown`. `Dashboard/1A`–`6A` were
in the export but had no binding, so they read zero. Either state tells an analysis agent
that the provider is broken or not realistic. The export files are read-only, and a fresh
export would also change unrelated points.

## Decision

Add `reference/graphene/demo-twin-supplement-tag-instances.json`. It has the same shape as
the instance export, and the loader walks it after the export. It holds only tags that are
missing from the export (70 points), so the Asset Model grows from 8,741 to 8,811 points.
None of the export's points change. Every supplement point is bound to simulated physics,
never to a fallback:

- `DemoRack/RCMS425` reads residual current on the rack incomer's protective earth. It
  follows the E820 load and rises with a breaker earth fault.
- Per-hall facility power is the hall's IT power, the non-IT consumers in the hall and its
  UPS losses, plus the rest of the facility overhead shared by IT power. The halls' shares
  add up to the site's facility power. Hall PUE, `Dashboard/Data Halls` and
  `Dashboard/1A`–`6A` (DH01–DH06) all derive from it.
- The carbon footprint uses the gateway's demo rates and factors, together with the site's
  facility power and its year-average facility power.

Energy registers (IT equipment, and each floor's energy per load class) start at their
year-to-date value, not at zero. This keeps Dashboard energy totals consistent with Scope 2
grid energy YTD after every restart.

## Consequences

- The Asset Model contract (point count and checksum) now includes the supplement. When a
  new export contains a supplement tag, remove that tag from the supplement, or the loader
  raises on the duplicate path.
- On the gateway, supplement tags must point at `Graphene Demo Twin` with
  `ns=2;s=point:<encodedExportPath>` (ADR-0005). Members of UDT instances such as
  `RCMS425-D` need that as an instance override.
