"""Point bindings for PlaceholderHallDomain; P1's thermal and electrical physics replace them."""

from collections.abc import Callable, Iterable

from graphene_demo_twin.asset_model import AssetModel
from graphene_demo_twin.plant_design import ConnectionKind, PlantDesign
from graphene_demo_twin.projection.projector import Binding
from graphene_demo_twin.sim import WorldState

HOT_AISLE_TYPE = "Temperature and Humidity"
BRANCH_METER_TYPE = "BCPM"


def placeholder_bindings(asset_model: AssetModel, design: PlantDesign) -> list[Binding]:
    """Per Data Hall: hot-aisle sensors in the hall read its air temperature, the branch circuit
    monitors that supply it (over its authored power connections) split its IT Load, and the
    Dashboard Plant View sums IT Load and IT energy."""
    halls = [r for r in design.rooms.values() if r.kind == "hall"]
    bindings: list[Binding] = []
    for hall in halls:
        sensors = [a for a in design.assets_in(hall.id) if a.type_id == HOT_AISLE_TYPE]
        meters = [
            path
            for path in design.upstream(hall.id, ConnectionKind.POWER)
            if path in design.assets and design.asset(path).type_id == BRANCH_METER_TYPE
        ]
        for sensor in sensors:
            bindings.append(Binding(f"{sensor.path}/Temp", _var(hall.id, "temp_c")))
        for meter in meters:
            share = 1.0 / len(meters)
            bindings.append(Binding(f"{meter}/Active Power", _var(hall.id, "it_load_kw", share)))
        bindings.append(
            Binding(
                f"Dashboard/Energy/Floors/{hall.floor}/Data Halls/{hall.id}/IT Energy",
                _var(hall.id, "it_energy_kwh"),
            )
        )
    for floor in {h.floor for h in halls}:
        on_floor = [h.id for h in halls if h.floor == floor]
        bindings.append(
            Binding(
                f"Dashboard/Energy/Floors/{floor}/Data Halls/IT Energy",
                _sum(on_floor, "it_energy_kwh"),
            )
        )
    every = [h.id for h in halls]
    bindings += [
        Binding("Dashboard/Energy/Building/Total IT Energy", _sum(every, "it_energy_kwh")),
        Binding("Dashboard/Total IT Load", _sum(every, "it_load_kw")),
        Binding("Dashboard/Total IT Load 2", _sum(every, "it_load_kw", 1e-3)),
    ]
    missing = [b.path for b in bindings if b.path not in asset_model.points]
    if missing:
        raise ValueError(f"placeholder bindings name points not in the Asset Model: {missing}")
    return sorted(bindings, key=lambda b: b.path)


def _var(node: str, name: str, scale: float = 1.0) -> Callable[[WorldState], float]:
    def read(state: WorldState) -> float:
        return state.assets[node][name] * scale

    return read


def _sum(nodes: Iterable[str], name: str, scale: float = 1.0) -> Callable[[WorldState], float]:
    nodes = tuple(nodes)

    def read(state: WorldState) -> float:
        return sum(state.assets[n][name] for n in nodes) * scale

    return read
