# Project State

Current state: **IMPLEMENTED / VERIFYING**.

The runtime now contains the first asset-state + network-balance physical-world slice for Chiller, Cooling Tower, Chiller Pump and CRAC equipment. Physical fault activations are converted into solver constraints before point projection. Cooling-tower unavailability propagates through existing `serves` topology to condenser-water conditions, chiller COP/input power and facility electrical balance. CRAC valve faults similarly affect valve feedback, CHW flow, supply-air temperature, unmet cooling and hall temperature. Real Chiller System Control setpoints now drive CHWS state, load-limited Chiller capacity and minimum/maximum staging; aggregate Plant Load, Required Chillers and Running Chillers are projected from the solved control state.

The Graphene export contract remains unchanged: export paths are immutable and no generated schema points are removed or renamed. Raw overrides still apply only after authoritative physics/fault calculation.

Domains outside the cooling-plant slice still use deterministic compatibility sources and are not yet claimed to have complete physical causality. Future migrations should follow the same AssetState / NetworkBalance pattern rather than adding independent point heuristics.
