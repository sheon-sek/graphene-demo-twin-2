# 3D Digital Twin Design System

This scene follows the `3dviz-pro-max` workflow: authoritative state first, recognizable object families second, then material/light/camera refinement and measured performance.

## Visual intent

The Virtual World page is an industrial operations model, not a game HUD. The first view should communicate facility scale and system separation while preserving a restrained dark operations-console palette. Fault red is reserved for authoritative fault sources; selection uses a separate cyan outline.

## Spatial model

Asset positions are generated deterministically from Graphene asset metadata. No individual asset has a hard-coded coordinate. Assets are grouped into Cooling Plant, Data Hall Airside, Electrical, Network, Water / Utility, Liquid Cooling, and Facility / Safety areas. Dense environmental/safety sensors use their own compact placement pass so enabling them does not collapse the primary equipment layout.

Topology lines are derived only from backend `WorldTopology` relations. The default overview shows `serves` and `networkParent`; selecting an asset reveals its `feeds` relationships as well. The frontend does not invent dependency edges.

## Object identity

Major equipment families use distinct low-poly procedural constructions rather than recolored boxes:

- Chillers: cabinet, compressor bodies, display, and pipe stubs.
- Pumps: motor, pump housing, shaft, and base.
- Valves: pipe body, valve body, stem, and handwheel.
- Cooling towers: hourglass tower mass and fan rim.
- CRAC/PAHU/FWU/FCU: cabinet proportions, intake/vent face, display.
- UPS/panels: tall electrical cabinet, display, vent/panel region.
- Gensets: enclosure, service panel, exhaust stack.
- Network switches/devices: rack-unit silhouette and port strip.
- Tanks: cylindrical vessel, reinforcement bands, top fitting.
- CDU: liquid-cooling cabinet, display, paired pipe connections.
- Environmental/leak sensors: compact mast/sensor head.

The models are deliberately schematic rather than BIM-level. Their role is recognition, scale, selection, topology inspection, and runtime-state communication.

## Materials and lighting

Equipment uses restrained metallic/rough surfaces with family-specific muted colors. A hemisphere fill and one directional key establish readable form without fake practical lights. The key owns the scene-wide shadow direction; area floors receive contact shadows. Emissive display surfaces are visual indicators only and do not claim to illuminate the facility.

## Motion and authoritative state

The scene uses `frameloop="demand"` for both healthy and faulted states. Fault presence is shown with a static high-contrast ring derived from runtime fault activation; the frontend does not animate a fault merely to attract attention, and it never creates or changes process state. Camera transitions explicitly invalidate frames while moving.

Camera focus interpolates toward the selected asset. Area presets change framing only; they do not mutate selection or runtime state. Orbit/pan/zoom remain available after any preset.

## Performance constraints

- Render assets, not 8k+ Graphene points.
- Clamp DPR to 1–1.35.
- Keep procedural models low-poly.
- Avoid per-frame world rebuilds; layout and topology links are memoized.
- Environment sensors are disabled by default and opt-in as a layer.
- Switch-port telemetry remains in topology/inspection but the 192 ports are not rendered as facility-scale equipment meshes.
- All steady states use demand rendering; camera interaction/transition invalidates only the frames it needs.
