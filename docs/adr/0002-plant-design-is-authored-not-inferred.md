# The Plant Design is authored, not inferred

The Ignition export says which assets exist, and hints at where they are (floors, Data Halls, `G_`/`L1_`/`R_` prefixes). It says nothing about how they are connected. v1 inferred electrical, hydraulic and airside relations from names and ended up with thermal zones and service links the site does not have. v2 hand-authors a reviewed Plant Design instead: every asset's physical connections (electrical, hydraulic, airside, network, water) and its position in 3D space. The simulation propagates only along these authored connections, and the 3D scene renders only these positions and routes.

## Considered Options

- Keep inferring from the export: cheap, but produces plausible-looking wrong causality. That misleads both the operator and any AI diagnosing from the evidence.
- Wait for the real single-line diagram, P&ID and floor plans: none are available. If they arrive later, they replace the authored design, which is a data change and not an architecture change.
