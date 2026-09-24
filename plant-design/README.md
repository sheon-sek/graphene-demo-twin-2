# Plant Design

The site's hand-authored physical design (ADR-0002): rooms and floors, the service shafts between them, where every asset sits, and how assets connect electrically, hydraulically, on the airside, over the network and through the water system. The simulation propagates only along these connections, and the 3D Operator Console draws only these positions.

- `author.py`: the authored source. Edit this file, never the JSON.
- `plant-design.json`: generated output, which the engine loads. Regenerate it with `python plant-design/author.py plant-design/plant-design.json`.
- `graphene_demo_twin.plant_design.load_plant_design` loads the JSON and validates it against the Asset Model. A test regenerates the JSON and fails if it differs from the committed file, so commit both files together.

Rev 0.1 was approved on 2026-09-23 with review items A1–A14 accepted as drafted. The review page is https://claude.ai/artifact/FdHDsz3THQMf2Hj8iAYqwn.

Rev 0.2 (2026-09-23) adds the service shafts (review item A15, not yet reviewed). There are five risers beside the lift core, one per discipline, each running from Ground to Roof. Every connection that changes floor runs up the shaft that carries its kind. The loader rejects a shaft that sits outside the rooms of any floor it passes through, a connection kind carried by two shafts, and a connection that changes floor with no shaft of its kind reaching both floors. The Operator Console routes its risers from these shafts.

Rev 0.3 (2026-09-23) authors the load side of the power graph for #19 (review item A16, not yet reviewed): every electrical consumer, including each room's lighting, hangs off exactly one board or sub-meter, so the meter tree can balance. Meter14 (lifts) and Meter16 (genset auxiliaries) have no authored loads, because the gensets are sources and wiring their auxiliaries back to them would loop the graph; the electrical model gives them a stand-in draw. The air units each sub-meter feeds are listed by path, never selected by name prefix. The three-phase sub-meters Meter14, 17 and 19 hang off the essential-services board DB_24, since the loader rejects a three-phase meter fed from a single-phase one (DB_22 is a GEM230). The hall UPS modules are rated at 0.8 kVA per kW of their hall's design IT load (800 kVA, 960 kVA in DH08), so any two carry it at under 70 %, and the gensets at 3,750 kVA / 3,000 kW, so two carry their side.

Rev 0.4 (2026-09-24) adds the ceiling cooling units' air connections for #22 (review item A17, not yet reviewed). Each hall's CCU supplies air to that hall, so its cooling reaches the hall's heat balance like the other units'. The CDUs' air connection to DH08 carries no air: they remove the liquid-cooled share of its IT Load directly.

Rev 0.5 (2026-09-24) adds fire protection and the lifts for #26 (review item A18, not yet reviewed). The `Fire Protection System` and `Lift Monitoring System` folders hold loose points with no UDT, so every fire zone (`~FZ-L1-Z2`), every device in it (`~L1-Z2-SD1`: smoke and heat detectors, call points, alarm valves, fire pumps) and every lift (`~LIFT-1`–`3`) is an Unexported Asset observed through its folder. The loader accepts those two folders as observation paths. A zone and its devices stand in the zone's first room, where a fire in that zone burns. Every relation the simulation uses is an authored `fire` connection (carried up the control-network & fire-alarm riser): each zone reports its detectors, call points and alarm valves, holds its rooms, shuts down the PAHUs supplying air into them and recalls the lifts, and each alarm valve starts the fire pumps. Meter14 feeds the lifts and the essential-services board DB_24 the fire pumps. They report through GATEWAY B, which now also carries `Life Safety`.

Key decisions the design encodes:

- One chiller plant with four chillers. CH-004 is an Unexported Asset (ADR-0004).
- The roof weather station (`~WX-01`, observed through `Chiller System Control/Weather`) and each Data Hall's IT equipment (`~IT-DH01`–`08`, observed through its `Dashboard/Energy/…/Data Halls/DHnn` folder and its `Environment Monitoring/<floor>/DHnn` hall aggregates) are Unexported Assets, added for #18. The loader accepts a Data Hall's Environment Monitoring folder as an observation path only for its loose aggregate points, not its sensors. The BCPM branches feed the IT equipment, not the hall room. `basis.it` carries each hall's design kW, operating band and liquid-cooled share as numbers.
- CRAC units are DX and do not depend on chilled water.
- The electrical system is 2N at the MSBs. Each Data Hall has three UPS modules in a distributed-redundant arrangement, and BCPM nL1–3 meter their outputs.
- Root `Breaker/`, `Line/` and `Smart Alarm Logic/` are Support Assets, not the site single-line diagram.
