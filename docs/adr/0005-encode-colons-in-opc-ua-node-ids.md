# Encode colons in OPC UA point NodeIds

Status: accepted
Date: 2026-09-27

## Context

The Asset Model includes point names containing colons, including the Genset voltage
members `AC Voltage: L1-N`, `AC Voltage: L2-N`, and `AC Voltage: L3-N`. Some OPC UA
clients parse colons in an Item Path as syntax and cannot reliably address those NodeIds.
Renaming the Asset Model points is not acceptable: their export paths and member names are
the frozen Ignition contract.

## Decision

Keep every point's BrowseName and folder hierarchy exactly as exported. In string NodeId
identifiers, encode `%` as `%25` and `:` as `%3A` in the export-path suffix after `point:`.
Escape `%` first so the encoding is unambiguous. All other path characters remain
unchanged. Thus `Genset/Genset 1/AC Voltage: L1-N` is addressed as
`point:Genset/Genset 1/AC Voltage%3A L1-N` while it remains browsable under its original
name.

## Consequences

- Ignition OPC Item Paths for colon-bearing points must use the encoded NodeId suffix.
- The Asset Model, REST/SSE paths, OPC UA BrowseNames, and folder hierarchy do not change.
- Existing NodeIds without `%` or `:` remain unchanged.
