# Plant Design

The site's hand-authored physical design (ADR-0002): rooms and floors, the service shafts between them, where every asset sits, and how assets connect electrically, hydraulically, on the airside, over the network and through the water system. The simulation propagates only along these connections, and the 3D Operator Console draws only these positions.

- `author.py`: the authored source. Edit this file, never the JSON.
- `plant-design.json`: generated output, which the engine loads. Regenerate it with `python plant-design/author.py plant-design/plant-design.json`.
- `graphene_demo_twin.plant_design.load_plant_design` loads the JSON and validates it against the Asset Model. A test regenerates the JSON and fails if it differs from the committed file, so commit both files together.

Rev 0.1 was approved on 2026-09-23 with review items A1–A14 accepted as drafted. The review page is https://claude.ai/artifact/FdHDsz3THQMf2Hj8iAYqwn.

Rev 0.2 (2026-09-23) adds the service shafts (review item A15, not yet reviewed). There are five risers beside the lift core, one per discipline, each running from Ground to Roof. Every connection that changes floor runs up the shaft that carries its kind. The loader rejects a shaft that sits outside the rooms of any floor it passes through, a connection kind carried by two shafts, and a connection that changes floor with no shaft of its kind reaching both floors. The Operator Console routes its risers from these shafts.

Key decisions the design encodes:

- One chiller plant with four chillers. CH-004 is an Unexported Asset (ADR-0004).
- The roof weather station (`~WX-01`, observed through `Chiller System Control/Weather`) and each Data Hall's IT equipment (`~IT-DH01`–`08`, observed through its `Dashboard/Energy/…/Data Halls/DHnn` folder) are Unexported Assets, added for #18. The BCPM branches feed the IT equipment, not the hall room. `basis.it` carries each hall's design kW, operating band and liquid-cooled share as numbers.
- CRAC units are DX and do not depend on chilled water.
- The electrical system is 2N at the MSBs. Each Data Hall has three UPS modules in a distributed-redundant arrangement, and BCPM nL1–3 meter their outputs.
- Root `Breaker/`, `Line/` and `Smart Alarm Logic/` are Support Assets, not the site single-line diagram.
