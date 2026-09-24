"""Plant Design lookups the P0 stand-in models shared. The control-network stand-in they held
is replaced by `sim.network`, and the DX CRAC units by `sim.airside`."""

import functools

from graphene_demo_twin.plant_design import PlantDesign


@functools.cache
def assets_of(design: PlantDesign, type_id: str) -> tuple[str, ...]:
    return tuple(a.path for a in design.assets.values() if a.type_id == type_id and a.room)
