"""Point bindings for the fire protection devices and the lifts (`sim.life_safety`).

Each `Fire Protection System/<floor>/<zone>/<device>` Boolean is its device's panel state: true
in alarm (a detector detecting, a call point pressed, an alarm valve flowing, a fire pump
running) or in fault. Each `Lift Monitoring System/Lift n` reports its car.
"""

from collections.abc import Callable

from graphene_demo_twin.asset_model import AssetModel
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.projection.projector import Binding, VariableRead
from graphene_demo_twin.sim import Scalar, WorldState
from graphene_demo_twin.sim.life_safety import FIRE_DEVICES, LIFT


def life_safety_bindings(asset_model: AssetModel, design: PlantDesign) -> list[Binding]:
    bindings = []
    for u in design.unexported.values():
        if u.type_id in FIRE_DEVICES:
            device = u.id.rsplit("-", 1)[1]
            bindings.append(Binding(f"{u.observed_by[0]}/{device}", VariableRead(u.id, "on")))
        elif u.type_id == LIFT:
            folder = u.observed_by[0]
            bindings += [
                Binding(f"{folder}/Lift Level", VariableRead(u.id, "level")),
                Binding(f"{folder}/Direction", VariableRead(u.id, "direction")),
                Binding(f"{folder}/Door Status", VariableRead(u.id, "door")),
                Binding(f"{folder}/Moving Until", _epoch_ms(u.id)),
            ]
    missing = [b.path for b in bindings if b.path not in asset_model.points]
    if missing:
        raise ValueError(f"life-safety bindings name points not in the Asset Model: {missing}")
    return bindings


def _epoch_ms(lift: str) -> Callable[[WorldState], Scalar]:
    return lambda state: state.assets[lift]["moving_until"] * 1000
